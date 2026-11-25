"""Cau noi giua giao dien web va engine NOVA PROFILE.

Giao dien (HTML/JS trong cua so pywebview) goi cac phuong thuc cua
:class:`Bridge` qua ``window.pywebview.api``.  Moi thao tac di qua
kho profile, bo kiem tra trung va bo khoi chay -- giao dien khong co
duong di rieng.
"""

from __future__ import annotations

import secrets
import threading
from typing import Any

from ..core.collision import CollisionGuard
from ..core.fleet import _distinct_machines, generate_fleet
from ..core.generator import ProfileGenerator
from ..core.seed import new_master_seed, new_profile_id
from ..schema.network import Proxy
from . import defaults as defaults_store

DEFAULT_PRESET = "pixel-8-pro-android14"


def _random_preset_id() -> str:
    """Draw one machine out of the catalogue, at random.

    The draw is the one the fleet makes: the catalogue is reduced to
    the machines that are actually different from one another, and one
    of them is drawn at random out of that set.  A profile created on
    its own therefore belongs to the same population as the ones
    created in bulk, instead of always landing on one fixed handset.

    The draw is random rather than "the best machine", because the
    fleet spreads over the catalogue over many units and this is one
    unit: a person who creates a profile, sees what the app gives and
    creates another, must not get the same handset twice in a row.
    """

    from ..core.devices import all_presets

    machines = _distinct_machines(all_presets())
    if not machines:
        return DEFAULT_PRESET
    index = secrets.randbelow(len(machines))
    return machines[index].preset_id


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
        """Tao ``count`` profile.  Moi profile la mot may khac nhau, khong trung.

        Mot profile duoc tao cung dien mot may: thiet bi, GPU, man hinh,
        chip, RAM, ban Android va phien ban Chrome deu do bo may chon
        ngau nhien, khong phai mot mau co dinh.  Do la ly do mot profile
        don le cung dien qua bo may chon ngay, va khong phai mot mau
        co dinh nao.
        """

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
        """Build a single profile, on a machine drawn from the catalogue.

        The machine is drawn the way the fleet draws it -- from the
        spread of the presets, not from one fixed handset -- so a
        profile created one at a time belongs to the same population as
        the ones created in bulk.
        """

        master = new_master_seed()
        candidate = ProfileGenerator().generate(
            preset_id=_random_preset_id(),
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
    # Nhom: mo / dung / gan proxy / phan tich
    # ------------------------------------------------------------------

    def open_profiles(self, profile_ids: list[str]) -> dict[str, Any]:
        """Mo mot nhom profile.

        Moi profile duoc mo rieng va mot loi cua mot profile khong
        chong lai nhom: mot may the nao that bai thi cac may khac van
        chay.  Ket qua tra ve danh sach may da mo, danh sach may that
        bai, va loi cua may that bai.
        """

        with self._lock:
            self._ensure()
            opened: list[str] = []
            failed: list[dict[str, str]] = []
            for pid in profile_ids or []:
                try:
                    config = self._store.get_profile(pid)
                    if config is None:
                        failed.append({"id": pid, "error": "Profile khong ton tai."})
                        continue
                    self._launcher.launch(config)
                    self._store.mark_launched(pid)
                    opened.append(pid)
                except Exception as exc:
                    failed.append({"id": pid, "error": str(exc)})
            return {"ok": True, "opened": opened, "failed": failed}

    def close_profiles(self, profile_ids: list[str]) -> dict[str, Any]:
        """Dung mot nhom profile."""

        with self._lock:
            self._ensure()
            stopped: list[str] = []
            failed: list[dict[str, str]] = []
            for pid in profile_ids or []:
                try:
                    self._launcher.terminate(pid)
                    self._store.mark_closed(pid)
                    stopped.append(pid)
                except Exception as exc:
                    failed.append({"id": pid, "error": str(exc)})
            return {"ok": True, "stopped": stopped, "failed": failed}

    def assign_proxies(
        self,
        profile_ids: list[str],
        proxies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Gan proxy cho cac profile theo thu tu.

        ``proxies`` la danh sach, va phan tu thu ``i`` cua no di
        vao profile thu ``i`` cua ``profile_ids``.  Do la thu tu cua
        checkbox: nguoi dung tick ba profile va dan ba dong proxy,
        dong mot vao profile dau tien, dong hai vao profile thu hai.

        Mot dong proxy co the nhieu dinh dang, va ca nhung dinh dang
        deu doc duoc:

        ``host:port``            -- HTTP, khong co ten dang nhap
        ``host:port:user:pass``  -- HTTP, co ten dang nhap
        ``user:pass@host:port``  -- HTTP, co ten dang nhap
        ``http://host:port``     -- HTTP, khong co ten dang nhap
        ``https://...``, ``socks5://...`` -- theo giao thuc trong chuoi

        Dong nao rong se bi bo qua, va profile tuong ung giu proxy cu
        no -- nen danh sach ngan hon danh sach profile khong xoa
        proxy cua profile nao.
        """

        with self._lock:
            self._ensure()
            ids = [str(p) for p in (profile_ids or [])]
            lines = list(proxies or [])
            assigned = 0
            failed: list[dict[str, str]] = []
            for index, pid in enumerate(ids):
                if index >= len(lines):
                    break
                raw = lines[index]
                parsed = _parse_proxy_line(raw)
                if parsed is None:
                    continue
                try:
                    config = self._store.get_profile(pid)
                    if config is None:
                        failed.append({"id": pid, "error": "Profile khong ton tai."})
                        continue
                    px = _proxy_from(parsed)
                    update: dict[str, Any] = {"proxy": px}
                    if px.enabled:
                        # Mot profile co proxy phai chan WebRTC lo IP that.
                        update["webrtc"] = config.webrtc.model_copy(
                            update={
                                "ice_transport_policy": "relay",
                                "block_non_proxied_udp": True,
                            }
                        )
                    self._store.update_profile(config.model_copy(update=update))
                    assigned += 1
                except Exception as exc:
                    failed.append({"id": pid, "error": str(exc)})
            return {"ok": True, "assigned": assigned, "failed": failed}

    def analyze_selection(self, profile_ids: list[str]) -> dict[str, Any]:
        """Phan tich nhom profile da chon.

        Tra ve so do cac thuoc do doc duoc cua nhom: may dang chay,
        may da dung, cac thiet bi khac nhau, cac GPU khac nhau, cac
        proxy khac nhau, va cac ban Android khac nhau.  Nhom ma co
        nhieu profile cung mot thiet bi, cung mot GPU va cung mot proxy
        la nhom ma mot trang web se gom lai mot nguoi -- va do la thu
        can thay ngay khi con so profile lon len.
        """

        with self._lock:
            self._ensure()
            try:
                running = set(self._launcher.launched)
            except Exception:
                running = set()
            ids = [str(p) for p in (profile_ids or [])]
            configs = []
            missing: list[str] = []
            for pid in ids:
                config = self._store.get_profile(pid)
                if config is None:
                    missing.append(pid)
                    continue
                configs.append(config)

            def _distinct(getter: Any) -> int:
                values = set()
                for config in configs:
                    values.add(getter(config))
                return len(values)

            proxies = set()
            for config in configs:
                px = getattr(config, "proxy", None)
                if px is not None and getattr(px, "enabled", False) and getattr(px, "host", ""):
                    proxies.add(f"{px.protocol}://{px.host}:{px.port}")
                else:
                    proxies.add("")

            return {
                "ok": True,
                "selected": len(ids),
                "found": len(configs),
                "missing": missing,
                "running": sum(1 for c in configs if c.identity.id in running),
                "stopped": sum(1 for c in configs if c.identity.id not in running),
                "distinct_devices": _distinct(
                    lambda c: str(getattr(getattr(c, "android_build", None), "model", "") or "")
                ),
                "distinct_gpus": _distinct(
                    lambda c: str(getattr(getattr(c, "webgl", None), "unmasked_renderer", "") or "")
                ),
                "distinct_androids": _distinct(
                    lambda c: str(getattr(getattr(c, "android_build", None), "version_release", "") or "")
                ),
                "distinct_proxies": len(proxies),
                "with_proxy": sum(1 for p in proxies if p),
                "without_proxy": sum(1 for p in proxies if not p),
            }

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


def _parse_proxy_line(line: Any) -> dict[str, Any] | None:
    """Read one line of a proxy list.

    The lines a person pastes come from every proxy vendor there is,
    so the reader takes the shapes that are actually written down:

    ``host:port``            -- HTTP, no credentials
    ``host:port:user:pass``  -- HTTP, with credentials
    ``user:pass@host:port``  -- HTTP, with credentials
    ``scheme://host:port``   -- the scheme of the URI is the protocol

    A line that is empty, or that carries no host, is not a proxy and
    is reported as ``None`` so the caller can skip it and leave the
    profile alone.
    """

    if line is None:
        return None
    text = str(line).strip()
    if not text:
        return None

    mode = "http"
    if "://" in text:
        scheme, text = text.split("://", 1)
        scheme = scheme.strip().lower()
        if scheme in ("http", "https", "socks4", "socks5", "socks5h", "ssh"):
            mode = scheme
            # The scheme "http" is the default, so it is left off the
            # form: a list written for a vendor that only sells HTTP
            # proxies does not have to carry it on every line.
        text = text.strip()

    username = ""
    password = ""
    if "@" in text:
        credentials, text = text.rsplit("@", 1)
        if ":" in credentials:
            username, password = credentials.split(":", 1)
        else:
            username = credentials

    parts = [p.strip() for p in text.split(":")]
    parts = [p for p in parts if p != ""]
    if not parts:
        return None

    host = parts[0]
    port = 0
    if len(parts) > 1:
        try:
            port = int(parts[1])
        except ValueError:
            port = 0
    # The "host:port:user:pass" shape has no marker of its own, so it
    # is recognised by the number of the parts and by the fact that
    # the credentials are not already known.
    if len(parts) > 2 and not username:
        username = parts[2]
    if len(parts) > 3 and not password:
        password = parts[3]

    if not host:
        return None
    if not (0 < port <= 65535):
        return None

    return {
        "mode": mode,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
    }


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
