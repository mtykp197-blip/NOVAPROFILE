# NOVA Profile Engine -- Agent Contract

> This file is the **interface contract** for parallel agents working on this
> codebase. Every agent MUST read it before writing code. Do not invent new
> module names that are not listed in the "File Ownership" table.

## 0. Hard rules

1. **Python 3.12.** Use `python` from `PATH` (or `py -3.12`). The
   repository does not pin an absolute interpreter path, because that path
   is specific to the machine the work was done on. Use `python -m pytest`.
2. **Every module must be importable standalone.** No circular imports.
   Import siblings via absolute paths (`from novaprofile.schema.hardware import ...`).
3. **Type hints everywhere.** `from __future__ import annotations` at the top of
   every file.
4. **Pydantic v2** only (`field_validator`, `model_config = ConfigDict(...)`,
   `model_dump`, `model_validate`). Never Pydantic v1 APIs.
5. **No `Any` in public signatures** unless unavoidable. Use `dict[str, str]`,
   `list[str]`, etc.
6. **Do not create files outside your assigned ownership list.** If you need
   something from another module that does not exist yet, import it assuming the
   documented signature -- do NOT create a stub file for it.
7. **Do not modify** `novaprofile/schema/enums.py`, `novaprofile/schema/hardware.py`,
   `novaprofile/schema/identity.py` -- these are owned by the lead agent and are
   already written. Read them and import from them.
8. **Deterministic tests.** Every module you own must come with tests that
   pass deterministically with no network access.

## 1. Architecture overview

```
novaprofile/
  schema/        # Pydantic models, one file per spec group
    enums.py     # [LEAD] shared enums/literals
    hardware.py  # [LEAD] group 1 (build/ABI), 4 (screen/viewport), 6 (canvas/webgl/webgpu)
    identity.py  # [LEAD] group 3 (navigator/UA/chints/permissions), 5 (tz/locale/geo)
    signals.py   # [LEAD] group 7 (audio/rects/fonts/media/storage/battery/sensors)
    network.py   # [LEAD] group 2 (proxy/webrtc/dns/geo-intel/http headers)
    profile.py   # [LEAD] aggregate ProfileConfig + SeedConfig
  core/          # deterministic engine
    seed.py      # [LEAD] SeedDeriver (HKDF-SHA256, per-axis domain salts)
    consistency.py# [LEAD] ConsistencyResolver (cross-layer validation)
    collision.py # [LEAD] CollisionGuard (nudge -> roll seed -> reject)
    devices.py   # [LEAD] real Android device catalog (SoC/GPU/screen/build triples)
  registry/      # persistence
  network/       # proxy + geo intelligence
  launcher/      # process management
  security/      # encryption at rest
  probe/         # runtime probe + QA suite
  api/           # FastAPI control plane
  cli/           # Typer CLI
tests/
```

## 2. File ownership (DO NOT cross)

| Agent | Owns |
|---|---|
| **Lead** | `schema/enums.py`, `schema/hardware.py`, `schema/identity.py`, `schema/signals.py`, `schema/network.py`, `schema/profile.py`, `core/seed.py`, `core/consistency.py`, `core/collision.py`, `core/devices.py`, `core/__init__.py` |
| **A** | `registry/*.py`, `security/*.py` |
| **B** | `network/*.py` (except `network/proxy.py` which Lead writes) |
| **C** | `probe/*.py`, `launcher/*.py` |
| **D** | `api/*.py`, `cli/*.py` |

**Exception:** `network/proxy.py` is Lead-owned because `CollisionGuard` and the
launcher both depend on it. Agent B owns everything else in `network/`.

## 3. Frozen module signatures

Agents MUST code against exactly these signatures. If you believe a signature
is wrong, report it -- do not change it unilaterally.

### 3.1 `novaprofile.schema.profile`

```python
class SeedConfig(BaseModel):
    master_seed: str          # 64-char hex
    seed_epoch: int
    nudge_step: int
    hkdf_version: str
    preset_id: str
    core_version: str

class ProfileIdentity(BaseModel):
    id: str                   # UUIDv7
    name: str
    group_id: str | None
    folder_id: str | None
    tags: list[str]
    notes: str
    status: ProfileStatus
    created_at: datetime
    updated_at: datetime
    last_started_at: datetime | None
    last_closed_at: datetime | None
    total_runtime_ms: int
    launch_count: int

class ProfileConfig(BaseModel):
    """The complete, immutable payload handed to the browser core."""
    identity: ProfileIdentity
    seed_config: SeedConfig
    android_build: AndroidBuild
    android_abi_soc: AndroidAbiSoc
    navigator: Navigator
    user_agent_data: UserAgentData
    plugins_mime: PluginsMime
    permissions: Permissions
    screen: Screen
    orientation: Orientation
    viewport: Viewport
    media_queries: MediaQueries
    timezone: Timezone
    locale: Locale
    geolocation: Geolocation
    canvas_2d: Canvas2DConfig
    webgl: WebGLConfig
    webgpu: WebGPUConfig
    audio: Audio
    rects: Rects
    fonts: Fonts
    media_devices: MediaDevices
    storage_network: StorageNetwork
    battery_speech_gamepad: BatterySpeechGamepad
    sensors: Sensors
    proxy: Proxy
    proxy_rotation: ProxyRotation
    geo_intelligence: GeoIntelligence
    geo_sync: GeoSync
    webrtc: WebRtc
    dns: Dns
    http_headers: HttpHeaders
```

### 3.2 `novaprofile.core.seed`

```python
class Axis(str, Enum):
    ANDROID_BUILD = "android_build"
    NAVIGATOR = "navigator"
    CLIENT_HINTS = "client_hints"
    SCREEN_VIEWPORT = "screen_viewport"
    CANVAS_2D = "canvas_2d"
    WEBGL_PIXEL = "webgl_pixel"
    WEBGL_PARAMS = "webgl_params"
    WEBGPU = "webgpu"
    AUDIO = "audio"
    RECTS = "rects"
    FONTS = "fonts"
    MEDIA_DEVICES = "media_devices"
    SENSORS = "sensors"
    BATTERY_NETWORK = "battery_network"
    GEO_TIMEZONE = "geo_timezone"
    STORAGE_QUOTA = "storage_quota"

class SeedDeriver:
    HKDF_VERSION: str = "hkdf-sha256-v1"

    def __init__(self, master_seed: str, seed_epoch: int = 0, nudge_step: int = 0) -> None: ...

    @property
    def master_seed(self) -> bytes: ...
    @property
    def seed_epoch(self) -> int: ...
    @property
    def nudge_step(self) -> int: ...

    def axis_key(self, axis: Axis) -> bytes:
        """HKDF-Extract(salt=domain_salt(axis), ikm=master_seed) -> 32 bytes."""

    def derive_bytes(self, axis: Axis, length: int, context: str = "") -> bytes:
        """HKDF-Expand(prk=axis_key(axis), info=axis||context, L)."""

    def derive_int(self, axis: Axis, low: int, high: int, context: str = "") -> int:
        """Uniform integer in [low, high] (inclusive)."""

    def derive_float(self, axis: Axis, low: float, high: float, context: str = "") -> float: ...

    def derive_bool(self, axis: Axis, true_probability: float = 0.5, context: str = "") -> bool: ...

    def derive_choice(self, axis: Axis, options: Sequence[T], context: str = "") -> T: ...

    def derive_webgl_image_value(self, unmasked_renderer: str) -> str:
        """17+ significant digit decimal string. MUST bind unmasked_renderer
        into the hash so two different GPU strings never collide."""

    def webgl_image_value(self) -> str:
        """Convenience: derive with empty renderer (generic)."""
```

`derive_webgl_image_value` requirements:
- Must incorporate `master_seed`, `seed_epoch`, `nudge_step`, and
  `unmasked_renderer` into the digest.
- Must return a decimal string with **at least 17 significant digits**
  (use `repr(float)`-style shortest-round-trip, or format with `%.17g`).
- Must be **deterministic**: same inputs -> same output, always.
- Must differ when `nudge_step` increments by 1 (the nudge axis of the guard).

### 3.3 `novaprofile.core.collision`

```python
@dataclass(frozen=True)
class SignatureRecord:
    kind: SignatureKind
    signature: str
    profile_id: str
    seed_epoch: int
    nudge_step: int
    state: SignatureState
    created_at: datetime

class SignatureRegistry(Protocol):
    def find_active(self, kind: SignatureKind, signature: str) -> SignatureRecord | None: ...
    def insert(self, record: SignatureRecord) -> None: ...
    def count_active(self, kind: SignatureKind) -> int: ...

class CollisionGuard:
    MAX_ATTEMPTS: int = 8
    MAX_NUDGE_STEPS: int = 5

    def __init__(self, registry: SignatureRegistry, resolver, deriver_factory): ...

    def allocate(self, profile_id: str, candidate: ProfileConfig) -> ProfileConfig:
        """Run nudge -> roll seed -> reject. Raises ProfileCollisionError."""
```

`ProfileCollisionError` must be raised after `MAX_ATTEMPTS` failed attempts,
with a message naming the conflicting signature and the profile id.

### 3.4 `novaprofile.core.consistency`

```python
class ConsistencyIssue(BaseModel):
    severity: Literal["error", "warning"]
    axis: str
    message: str
    fields: list[str] = []

class ConsistencyReport(BaseModel):
    ok: bool
    errors: list[ConsistencyIssue]
    warnings: list[ConsistencyIssue]

class ConsistencyResolver:
    def check(self, config: ProfileConfig) -> ConsistencyReport: ...
    def resolve(self, config: ProfileConfig) -> ProfileConfig:
        """Apply deterministic fixes for warnings, re-derive dependent fields."""
```

### 3.5 `novaprofile.network.proxy`

```python
@dataclass(frozen=True)
class ProxyEndpoint:
    protocol: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def is_direct(self) -> bool: ...
    def to_url(self, redact: bool = False) -> str: ...
    def chrome_switch(self) -> str:
        """--proxy-server=... (scheme+host+port)"""

class ProxyResolver:
    def resolve(self, cfg: Proxy) -> ProxyEndpoint | None: ...
```

### 3.6 `novaprofile.security.crypto`

```python
def encrypt_secret(plaintext: str, *, key: bytes | None = None) -> str: ...
def decrypt_secret(ciphertext: str, *, key: bytes | None = None) -> str: ...
def get_master_key() -> bytes:
    """DPAPI-protected key on Windows, file fallback elsewhere."""
```

### 3.7 `novaprofile.probe.probe`

```python
@dataclass
class ProbeResult:
    axis: str
    ok: bool
    value: str
    detail: str = ""

@dataclass
class ProbeReport:
    profile_id: str
    ok: bool
    results: list[ProbeResult]
    distinct_ratio: float
    collision_rate: float
    persistence_ok: bool
    consistency_pass: bool

class RuntimeProbe:
    def __init__(self, chrome_path: str | None = None, headless: bool = True): ...
    def collect(self, config: ProfileConfig) -> ProbeReport: ...
```

### 3.8 `novaprofile.registry.store`

```python
class ProfileStore:
    def __init__(self, db_path: str | Path): ...
    def init_db(self) -> None: ...
    def create_profile(self, config: ProfileConfig) -> None: ...
    def get_profile(self, profile_id: str) -> ProfileConfig | None: ...
    def list_profiles(self, *, group_id=None, tag=None, status=None, search=None) -> list[ProfileConfig]: ...
    def update_profile(self, config: ProfileConfig) -> None: ...
    def delete_profile(self, profile_id: str) -> None: ...
    def record_signature(self, record: SignatureRecord) -> None: ...
    def find_signature(self, kind: SignatureKind, signature: str) -> SignatureRecord | None: ...
    def count_signatures(self, kind: SignatureKind) -> int: ...
```

## 4. Environment / storage layout

```
%LOCALAPPDATA%\NovaProfile\
  profiles\<profile_id>\        # user_data_dir for the browser
  state.db                      # SQLite: profiles + signatures
  keys\master.key               # DPAPI-protected AES key
  logs\<profile_id>\<session_id>.log
```

Use `platformdirs`-style logic manually (do NOT add a dependency):
`Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "NovaProfile"`.

## 5. Testing conventions

- Test files live in `tests/` at `control_plane/tests/`.
- Name them `test_<module>.py`.
- Use `tmp_path` for filesystem isolation.
- No network calls. Mock `httpx`/`aiohttp` where needed.
- Run: `python -m pytest tests -q`

## 6. Definition of done per agent

- Module imports cleanly: `python -c "import novaprofile.<pkg>.<mod>"`
- Tests pass: `python -m pytest tests -q`
- `ruff check` clean for files you own.
- No TODOs, no `pass  # TODO`, no placeholder functions.
