"""Dependency injection for the NOVA control-plane API.

The engine singletons live behind FastAPI dependencies so that tests can
override them with ``app.dependency_overrides`` and so that a missing engine
module produces a clean HTTP error rather than an import-time crash.

All lookups are cached with :func:`functools.lru_cache`; the cache is
clearable through :func:`reset_dependencies` which the test-suite uses to
isolate temporary stores.
"""

from __future__ import annotations

import dataclasses
import functools
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "EngineUnavailableError",
    "ModuleUnavailableError",
    "get_store",
    "get_guard",
    "get_resolver",
    "get_launcher",
    "get_probe",
    "get_profile_home",
    "get_state_db_path",
    "reset_dependencies",
    "available_modules",
]


class EngineUnavailableError(RuntimeError):
    """Raised when an engine module is not importable (yet).

    The API layer converts this into an HTTP 503 so that a partially built
    engine degrades gracefully instead of taking the whole control plane down.
    """


class ModuleUnavailableError(EngineUnavailableError):
    """Describes a specific engine module that could not be imported."""

    def __init__(self, module_name: str, reason: str) -> None:
        super().__init__(f"module {module_name!r} is not available yet: {reason}")
        self.module_name = module_name
        self.reason = reason


def _import(module_name: str) -> Any:
    """Import *module_name*, converting any failure into a domain error."""
    import importlib

    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - defensive, any import failure is fatal
        raise ModuleUnavailableError(module_name, str(exc)) from exc


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def get_profile_home() -> Path:
    """Return the NOVA application-data root.

    Honours ``$NOVA_PROFILE_HOME`` so that tests can point the whole engine at
    a temporary directory.
    """
    override = os.environ.get("NOVA_PROFILE_HOME")
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "NovaProfile"
    return Path.home() / ".local" / "share" / "NovaProfile"


def get_state_db_path() -> Path:
    """Return the path of the SQLite state database."""
    return get_profile_home() / "state.db"


# --------------------------------------------------------------------------
# Engine singletons
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def get_store() -> Any:
    """Return the process-wide :class:`~novaprofile.registry.store.ProfileStore`."""
    module = _import("novaprofile.registry.store")
    store_cls = getattr(module, "ProfileStore", None)
    if store_cls is None:
        raise ModuleUnavailableError("novaprofile.registry.store", "ProfileStore is missing")

    db_path = get_state_db_path()
    default_path = getattr(module, "default_db_path", None)
    if callable(default_path):
        try:
            db_path = Path(default_path())
        except Exception:  # noqa: BLE001 - defensive, fall back to our own path
            db_path = get_state_db_path()

    store = store_cls(str(db_path))
    init_db = getattr(store, "init_db", None)
    if callable(init_db):
        init_db()
    return store


@functools.lru_cache(maxsize=1)
def get_guard() -> Any:
    """Return the process-wide :class:`~novaprofile.core.collision.CollisionGuard`."""
    collision = _import("novaprofile.core.collision")
    consistency = _import("novaprofile.core.consistency")
    seed = _import("novaprofile.core.seed")

    guard_cls = getattr(collision, "CollisionGuard", None)
    resolver_cls = getattr(consistency, "ConsistencyResolver", None)
    deriver_cls = getattr(seed, "SeedDeriver", None)
    if guard_cls is None or resolver_cls is None or deriver_cls is None:
        raise ModuleUnavailableError(
            "novaprofile.core.collision",
            "CollisionGuard / ConsistencyResolver / SeedDeriver missing",
        )

    registry = _signature_registry()
    return guard_cls(registry, resolver_cls(), deriver_cls)


def _signature_registry() -> Any:
    """Build a signature registry backed by the profile store, if possible."""
    try:
        store = get_store()
    except EngineUnavailableError:
        store = None
    if store is not None and hasattr(store, "find_signature"):
        return store
    collision = _import("novaprofile.core.collision")
    in_memory = getattr(collision, "InMemorySignatureRegistry", None)
    if in_memory is not None:
        return in_memory()
    registry_cls = getattr(collision, "SignatureRegistry", None)
    if registry_cls is not None and not isinstance(registry_cls, type):
        return registry_cls
    raise ModuleUnavailableError(
        "novaprofile.core.collision", "no usable SignatureRegistry implementation"
    )


@functools.lru_cache(maxsize=1)
def get_resolver() -> Any:
    """Return the process-wide :class:`~novaprofile.core.consistency.ConsistencyResolver`."""
    module = _import("novaprofile.core.consistency")
    resolver_cls = getattr(module, "ConsistencyResolver", None)
    if resolver_cls is None:
        raise ModuleUnavailableError(
            "novaprofile.core.consistency", "ConsistencyResolver is missing"
        )
    return resolver_cls()


def _note_browser_exit(profile_id: str, code: int | None) -> None:
    """Write the status of a profile whose browser has closed.

    The operator can close the browser's own window, and that is not a
    call the interface makes.  The launcher watches the browser and
    tells the control plane when it is gone, and the control plane
    writes the status, so the table of profiles is right without the
    operator doing anything.
    """

    try:
        store = get_store()
    except Exception:  # noqa: BLE001 - the note is best effort
        return

    writer = getattr(store, "set_status", None)
    if not callable(writer):
        writer = getattr(store, "update_profile_status", None)
    if not callable(writer):
        return

    try:
        writer(profile_id, "ready")
    except Exception:  # noqa: BLE001 - the note is best effort
        pass


@functools.lru_cache(maxsize=1)
def get_launcher() -> Any:
    """Return the process-wide :class:`~novaprofile.launcher.launcher.BrowserLauncher`.

    The launcher is given a core manager, because a launcher without one
    can never select a core: it runs whatever executable it finds,
    which is a stock browser, and a stock browser does not know what
    ``--nova-config`` means -- so every surface the core owns is the
    host's, and the browser looks like a desktop rather than like the
    handset the profile claims to be.
    """
    module = _import("novaprofile.launcher.launcher")
    launcher_cls = getattr(module, "BrowserLauncher", None)
    if launcher_cls is None:
        raise ModuleUnavailableError("novaprofile.launcher.launcher", "BrowserLauncher is missing")

    kwargs: dict[str, Any] = {"on_exit": _note_browser_exit}

    # The manager that owns the installed cores.  It is optional: a
    # control plane without the core module still runs, it just has no
    # core to select.
    try:
        cores_module = _import("novaprofile.core.cores")
        manager_cls = getattr(cores_module, "CoreManager", None)
        if manager_cls is not None:
            kwargs["core_manager"] = manager_cls()
    except EngineUnavailableError:
        pass

    launcher = launcher_cls(**kwargs)
    profiles_root = get_profile_home() / "profiles"
    setter = getattr(launcher, "set_profiles_root", None)
    if callable(setter):
        setter(str(profiles_root))
    return launcher


@functools.lru_cache(maxsize=1)
def get_probe() -> Any:
    """Return the process-wide :class:`~novaprofile.probe.probe.RuntimeProbe`."""
    module = _import("novaprofile.probe.probe")
    probe_cls = getattr(module, "RuntimeProbe", None)
    if probe_cls is None:
        raise ModuleUnavailableError("novaprofile.probe.probe", "RuntimeProbe is missing")
    return probe_cls()


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------


def reset_dependencies() -> None:
    """Clear every cached engine singleton.

    Used by the test-suite between cases so that a temporary store is picked
    up again.
    """
    for factory in (
        get_store,
        get_guard,
        get_resolver,
        get_launcher,
        get_probe,
    ):
        factory.cache_clear()


def available_modules() -> dict[str, bool]:
    """Report which engine modules can currently be imported.

    Used by the ``doctor`` CLI command and the ``/api/health`` payload.
    """
    names = {
        "registry.store": "novaprofile.registry.store",
        "core.seed": "novaprofile.core.seed",
        "core.consistency": "novaprofile.core.consistency",
        "core.collision": "novaprofile.core.collision",
        "core.devices": "novaprofile.core.devices",
        "launcher": "novaprofile.launcher.launcher",
        "probe": "novaprofile.probe.probe",
        "network.proxy": "novaprofile.network.proxy",
    }
    result: dict[str, bool] = {}
    for label, module_name in names.items():
        try:
            _import(module_name)
        except EngineUnavailableError:
            result[label] = False
        else:
            result[label] = True
    return result


def call_or_raise[T](fn: Callable[[], T], module_name: str) -> T:
    """Call *fn* and translate import problems into :class:`ModuleUnavailableError`."""
    try:
        return fn()
    except ModuleUnavailableError:
        raise
    except ImportError as exc:
        raise ModuleUnavailableError(module_name, str(exc)) from exc


# --------------------------------------------------------------------------
# Shared service layer
#
# The API routes and the CLI both need to turn a preset plus a handful of
# overrides into a fully populated ``ProfileConfig``.  The helpers live here
# so that there is exactly one implementation of that logic.
# --------------------------------------------------------------------------


def get_profile_model() -> Any:
    """Return the :class:`~novaprofile.schema.profile.ProfileConfig` class."""
    module = _import("novaprofile.schema.profile")
    model = getattr(module, "ProfileConfig", None)
    if model is None:
        raise ModuleUnavailableError("novaprofile.schema.profile", "ProfileConfig is missing")
    return model


def get_identity_model() -> Any:
    """Return the :class:`~novaprofile.schema.profile.ProfileIdentity` class."""
    module = _import("novaprofile.schema.profile")
    model = getattr(module, "ProfileIdentity", None)
    if model is None:
        raise ModuleUnavailableError("novaprofile.schema.profile", "ProfileIdentity is missing")
    return model


def get_seed_model() -> Any:
    """Return the :class:`~novaprofile.schema.profile.SeedConfig` class."""
    module = _import("novaprofile.schema.profile")
    model = getattr(module, "SeedConfig", None)
    if model is None:
        raise ModuleUnavailableError("novaprofile.schema.profile", "SeedConfig is missing")
    return model


def get_device_catalog() -> Any:
    """Return the device-catalog module (``novaprofile.core.devices``)."""
    return _import("novaprofile.core.devices")


def get_generator() -> Any:
    """Return a callable that builds a ``ProfileConfig`` from a preset.

    The engine owns the profile assembly (the lead agent's ``core`` package).
    The generator is looked up under a couple of documented names so the
    control plane keeps working as the engine lands; when none of them exists
    the caller receives a clear :class:`ModuleUnavailableError` instead of an
    ``ImportError`` traceback.
    """
    for module_name in (
        "novaprofile.core.generator",
        "novaprofile.core.profile_generator",
        "novaprofile.core.builder",
        "novaprofile.core.devices",
    ):
        try:
            module = _import(module_name)
        except ModuleUnavailableError:
            continue
        for attr in (
            "generate_profile",
            "build_profile",
            "generate",
            "profile_from_preset",
        ):
            candidate = getattr(module, attr, None)
            if candidate is not None:
                return candidate
        generator_cls = getattr(module, "ProfileGenerator", None)
        if generator_cls is not None:
            return _GeneratorAdapter(generator_cls)
    raise ModuleUnavailableError(
        "novaprofile.core.generator",
        "no profile generator found (generate_profile / build_profile / ProfileGenerator)",
    )


class _GeneratorAdapter:
    """Adapts the engine's ``ProfileGenerator`` class to a plain callable.

    The engine exposes ``ProfileGenerator.generate(preset_id, *, name=...)``
    while the control plane prefers a single ``generate(preset_id, ...)``
    entry point with keyword arguments it controls.
    """

    def __init__(self, generator_cls: type) -> None:
        self._generator_cls = generator_cls
        self._instance: Any = None

    def _get(self) -> Any:
        if self._instance is None:
            self._instance = self._generator_cls()
        return self._instance

    def __call__(self, preset_id: str = "", **kwargs: Any) -> Any:
        instance = self._get()
        generate = getattr(instance, "generate", None)
        if not callable(generate):
            raise ModuleUnavailableError(
                "novaprofile.core.generator",
                f"{self._generator_cls.__name__} does not implement generate()",
            )
        accepted: dict[str, Any] = {}
        for key, value in kwargs.items():
            if value is None:
                continue
            accepted[key] = value
        return generate(preset_id, **accepted)


def list_presets() -> list[dict[str, Any]]:
    """Return the preset catalog as plain dictionaries.

    The catalog lives in ``novaprofile.core.devices``.  It is imported
    defensively: when it is not available an empty list is returned so that the
    ``GET /api/presets`` endpoint stays up.
    """
    try:
        catalog = get_device_catalog()
    except ModuleUnavailableError:
        return []

    for attr in ("list_presets", "presets", "PRESETS", "DEVICE_PRESETS", "DEVICE_CATALOG"):
        candidate = getattr(catalog, attr, None)
        if candidate is None:
            continue
        if callable(candidate):
            try:
                candidate = candidate()
            except Exception:  # noqa: BLE001 - defensive against partial catalog
                continue
        if isinstance(candidate, dict):
            return [_preset_to_dict(key, value) for key, value in candidate.items()]
        if isinstance(candidate, (list, tuple)):
            return [_preset_to_dict(None, item) for item in candidate]
    return []


def get_preset(preset_id: str) -> dict[str, Any] | None:
    """Return a single preset definition, or ``None`` when it is unknown."""
    for preset in list_presets():
        if str(preset.get("id", "")) == preset_id:
            return preset
    return None


def _preset_to_dict(key: str | None, value: Any) -> dict[str, Any]:
    """Normalise a catalog entry into a JSON-serialisable dictionary.

    Catalog entries may be plain dictionaries, Pydantic models or dataclasses
    (the ``DevicePreset`` entries are dataclasses holding further dataclasses).
    All three shapes are flattened into a plain dictionary.
    """
    data = _to_plain(value)
    if not isinstance(data, dict):
        data = {"value": str(value)}
    if "id" not in data:
        if key is not None:
            data["id"] = key
        else:
            data["id"] = str(data.get("preset_id", data.get("device_id", "unknown")))
    return data


def _to_plain(value: Any) -> Any:
    """Convert *value* into JSON-serialisable primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_plain(getattr(value, field.name, None))
            for field in dataclasses.fields(value)
        }
    return str(value)


def new_master_seed() -> str:
    """Return a fresh 64-character hexadecimal master seed."""
    import secrets

    return secrets.token_hex(32)


#: The placeholder that replaces a redacted secret.
REDACTED: str = "***redacted***"

#: The fields that are redacted unless the caller explicitly asks for them.
#:
#: The master seed regenerates the profile's entire fingerprint, so a copy
#: of it is a copy of the identity.  The proxy password is encrypted with
#: the installation's master key, which lives on the same machine, so the
#: ciphertext is not a meaningful barrier to whoever can call the API.
#: Neither belongs in a log, a screenshot or an ordinary response.
REDACTED_FIELDS: tuple[str, ...] = (
    "master_seed",
    "password",
)


def redact_secrets(payload: Any, _depth: int = 0) -> Any:
    """Replace the sensitive fields of a payload with a placeholder.

    The redaction is by *field name*, so it works on a plain dictionary as
    well as on a serialised model.
    """

    if _depth > 32:  # pragma: no cover - defensive against a cyclic payload
        return payload
    if isinstance(payload, dict):
        out: dict[Any, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key in REDACTED_FIELDS and value:
                out[key] = REDACTED
            else:
                out[key] = redact_secrets(value, _depth + 1)
        return out
    if isinstance(payload, list):
        return [redact_secrets(item, _depth + 1) for item in payload]
    return payload


def serialize_profile(config: Any, *, reveal_secrets: bool = False) -> dict[str, Any]:
    """Return a JSON-serialisable representation of a profile config.

    The sensitive fields are redacted by default.  A caller that genuinely
    needs them -- the launcher, for instance -- asks for them explicitly.
    """

    payload = _to_plain(config)
    if reveal_secrets:
        return payload
    return redact_secrets(payload)
