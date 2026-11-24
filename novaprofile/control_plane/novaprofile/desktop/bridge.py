"""Cau noi giua giao dien web va engine NOVA PROFILE.

Giao dien (HTML/JS trong cua so pywebview) goi cac phuong thuc cua
:class:`Bridge` qua ``window.pywebview.api``.  Moi thao tac di qua
kho profile, bo kiem tra trung va bo khoi chay -- giao dien khong co
duong di rieng.
"""

from __future__ import annotations

import threading
from typing import Any

from ..core.collision import CollisionGuard
from ..core.fleet import generate_fleet
from ..core.generator import ProfileGenerator
from ..core.seed import new_master_seed, new_profile_id
from ..schema.network import Proxy
from . import defaults as defaults_store

DEFAULT_PRESET = "pixel-8-pro-android14"


class Bridge:
    """Mot doi tuong duy nhat duoc phoi bay cho JavaScript."""

    def __init__(self, store: Any = None, launcher: Any = None, guard: Any = None) -> None:
        self._lock = threading.RLock()
        self._store = store
        self._launcher = launcher
        self._guard = guard
        self._window: Any = None

    # ------------------------------------------------------------------
    # Khoi tao (goi khi cua so san sang)
    # ------------------------------------------------------------------

    def attach_window(self, window: Any) -> None:
        self._window = window

    def _ensure(self) -> None:
        if self._store is not None:
            return
        from ..launcher.launcher import default_launcher
        from ..registry.store import ProfileStore, default_db_path

        store = ProfileStore(default_db_path())
        store.init_db()
        self._store = store
        self._guard = CollisionGuard(registry=store)
        # Phai nap loi NOVA da cai; khong co CoreManager thi launcher
        # quay ve Chrome thuong va trang web thay thong so that cua may.
        from ..core.cores import CoreManager

        self._launcher = default_launcher(core_manager=CoreManager())

    # ------------------------------------------------------------------
    # Doc du lieu
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure()
            running = {}
            try:
                running = {pid: True for pid in self._launcher.launched}
            except Exception:
                running = {}
            rows = []
            for config in self._store.list_profiles():
                rows.append(_summary(config, running))
            return rows

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            config = self._store.get_profile(profile_id)
            if config is None:
                return {"ok": False, "error": "Profile khong ton tai."}
            return {"ok": True, "profile": _detail(config)}

    def get_defaults(self) -> dict[str, Any]:
        return defaults_store.load_defaults()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            return {"count": len(self._store.list_profiles())}

    # ------------------------------------------------------------------
    # Tao profile
    # ------------------------------------------------------------------

    def create_profiles(self, name: str, count: int = 1, proxy: dict | None = None) -> dict[str, Any]:
        """Tao ``count`` profile.  Moi profile la mot may khac nhau, khong trung."""
        with self._lock:
            self._ensure()
            count = max(1, min(int(count or 1), 500))
            name = (name or "").strip() or "Profile"
            px = _proxy_from(proxy or defaults_store.load_defaults().get("proxy", {}))
            created: list[str] = []
            try:
                if count == 1:
                    configs = [self._one(name, px, None)]
                else:
                    configs = generate_fleet(
                        count,
                        name_prefix=name,
                        proxy=px if px.enabled else None,
                        registry=self._store,
                    )
                for c in configs:
                    self._persist(c)
                    created.append(c.identity.id)
                return {"ok": True, "created": len(created)}
            except Exception as exc:
                return {"ok": False, "error": f"Khong tao duoc: {exc}", "created": len(created)}

    def _one(self, name: str, px: Proxy, _unused: Any) -> Any:
        master = new_master_seed()
        candidate = ProfileGenerator().generate(
            preset_id=DEFAULT_PRESET,
            name=name,
            master_seed=master,
            profile_id=new_profile_id(),
            proxy=px if px.enabled else None,
        )
        return candidate

    def _persist(self, candidate: Any) -> None:
        pid = candidate.identity.id
        allocated = self._guard.allocate(pid, candidate)
        self._store.create_profile(allocated)
        self._guard.commit(allocated, pid)

    # ------------------------------------------------------------------
    # Sua / xoa
    # ------------------------------------------------------------------

    def update_profile(self, profile_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            config = self._store.get_profile(profile_id)
            if config is None:
                return {"ok": False, "error": "Profile khong ton tai."}
            try:
                if "name" in changes and str(changes["name"]).strip():
                    identity = config.identity.model_copy(update={"name": str(changes["name"]).strip()})
                    config = config.model_copy(update={"identity": identity})
                if "notes" in changes:
                    identity = config.identity.model_copy(update={"notes": str(changes["notes"])})
                    config = config.model_copy(update={"identity": identity})
                if "proxy" in changes and isinstance(changes["proxy"], dict):
                    px = _proxy_from(changes["proxy"])
                    update: dict[str, Any] = {"proxy": px}
                    if px.enabled:
                        # Mot profile co proxy phai chan WebRTC lo IP that.
                        update["webrtc"] = config.webrtc.model_copy(
                            update={"ice_transport_policy": "relay", "block_non_proxied_udp": True}
                        )
                    config = config.model_copy(update=update)
                self._store.update_profile(config)
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": f"Khong luu duoc: {exc}"}

    def rotate_fingerprint(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            config = self._store.get_profile(profile_id)
            if config is None:
                return {"ok": False, "error": "Profile khong ton tai."}
            try:
                rotated = self._guard.allocate(profile_id, config)
                if rotated.proxy.enabled and rotated.webrtc.ice_transport_policy != "relay":
                    rotated = rotated.model_copy(update={"webrtc": rotated.webrtc.model_copy(
                        update={"ice_transport_policy": "relay", "block_non_proxied_udp": True})})
                self._store.update_profile(rotated)
                self._guard.commit(rotated, profile_id)
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": f"Khong xoay duoc van tay: {exc}"}

    def delete_profiles(self, profile_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            done = 0
            for pid in profile_ids or []:
                try:
                    if self._launcher.is_running(pid):
                        self._launcher.terminate(pid)
                    self._store.delete_profile(pid)
                    done += 1
                except Exception:
                    continue
            return {"ok": True, "deleted": done}

    # ------------------------------------------------------------------
    # Mo / dung
    # ------------------------------------------------------------------

    def open_profile(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            config = self._store.get_profile(profile_id)
            if config is None:
                return {"ok": False, "error": "Profile khong ton tai."}
            try:
                self._launcher.launch(config)
                self._store.mark_launched(profile_id)
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": f"Khong mo duoc: {exc}"}

    def close_profile(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            try:
                ok = self._launcher.terminate(profile_id)
                self._store.mark_closed(profile_id)
                return {"ok": bool(ok)}
            except Exception as exc:
                return {"ok": False, "error": f"Khong dung duoc: {exc}"}

    # ------------------------------------------------------------------
    # Cai dat mac dinh
    # ------------------------------------------------------------------

    def save_defaults(self, values: dict[str, Any]) -> dict[str, Any]:
        try:
            defaults_store.save_defaults(values or {})
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ----------------------------------------------------------------------
# Chuyen doi du lieu
# ----------------------------------------------------------------------


def _proxy_from(raw: dict[str, Any]) -> Proxy:
    mode = str(raw.get("mode") or raw.get("protocol") or "none").lower()
    host = str(raw.get("host") or "").strip()
    enabled = mode not in ("none", "direct", "") and bool(host)
    protocol = mode if mode in ("http", "https", "socks4", "socks5", "socks5h", "ssh") else "direct"
    try:
        port = int(raw.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    return Proxy(
        enabled=enabled,
        protocol=protocol if enabled else "direct",
        host=host,
        port=port,
        username=str(raw.get("username") or ""),
        password=str(raw.get("password") or ""),
        auth_method="userpass" if raw.get("username") else "none",
    )


def _summary(config: Any, running: dict[str, bool]) -> dict[str, Any]:
    ident = config.identity
    android = getattr(config, "android_build", None)
    browser = getattr(config, "browser_version", None)
    proxy = getattr(config, "proxy", None)
    created = getattr(ident, "created_at", None)
    return {
        "id": ident.id,
        "name": ident.name,
        "device": str(getattr(android, "model", "") or ""),
        "android": str(getattr(android, "version_release", "") or ""),
        "chrome": str(getattr(browser, "full_version", "") or getattr(browser, "major", "") or ""),
        "proxy": _proxy_text(proxy),
        "created": str(created)[:16].replace("T", " ") if created else "",
        "status": "running" if running.get(ident.id) else (str(getattr(ident, "status", "") or "stopped")),
    }


def _detail(config: Any) -> dict[str, Any]:
    data = _summary(config, {})
    proxy = getattr(config, "proxy", None)
    data.update(
        {
            "notes": str(getattr(config.identity, "notes", "") or ""),
            "proxy_host": getattr(proxy, "host", "") if proxy else "",
            "proxy_port": getattr(proxy, "port", 0) if proxy else 0,
            "proxy_protocol": getattr(proxy, "protocol", "direct") if proxy else "direct",
            "proxy_user": getattr(proxy, "username", "") if proxy else "",
            "proxy_enabled": bool(getattr(proxy, "enabled", False)) if proxy else False,
        }
    )
    return data


def _proxy_text(proxy: Any) -> str:
    if proxy is None or not getattr(proxy, "enabled", False) or not proxy.host:
        return "Khong"
    return f"{proxy.protocol}://{proxy.host}:{proxy.port}"


__all__ = ["Bridge"]
