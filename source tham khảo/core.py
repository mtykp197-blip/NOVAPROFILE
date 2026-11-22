"""Core local-first: profile store, Android fingerprint, launcher, local API.

Code viet lai moi hoan toan. Khong sao chep tu Orbe/Hidemium.
Chi dung thu vien chuan Python + trinh duyet Chromium co san tren may.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import string
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
PROFILES_DIR = DATA_DIR / "profiles"
DEFAULTS_FILE = DATA_DIR / "defaults.json"

ANDROID_DEVICES = [
    {"model": "Pixel 8 Pro", "brand": "Google", "w": 1008, "h": 2244, "dpi": 490, "dpr": 2.625, "ram": 12},
    {"model": "Pixel 7", "brand": "Google", "w": 1080, "h": 2400, "dpi": 420, "dpr": 2.625, "ram": 8},
    {"model": "Galaxy S24", "brand": "Samsung", "w": 1080, "h": 2340, "dpi": 420, "dpr": 3.0, "ram": 8},
    {"model": "Galaxy A54", "brand": "Samsung", "w": 1080, "h": 2340, "dpi": 420, "dpr": 2.625, "ram": 6},
    {"model": "Redmi Note 13", "brand": "Xiaomi", "w": 1080, "h": 2400, "dpi": 440, "dpr": 2.75, "ram": 8},
    {"model": "POCO X6", "brand": "Xiaomi", "w": 1220, "h": 2712, "dpi": 446, "dpr": 2.75, "ram": 12},
    {"model": "OnePlus 12R", "brand": "OnePlus", "w": 1264, "h": 2780, "dpi": 450, "dpr": 3.0, "ram": 16},
    {"model": "Vivo V30", "brand": "Vivo", "w": 1260, "h": 2800, "dpi": 453, "dpr": 2.75, "ram": 12},
]

ANDROID_VERSIONS = ["12", "13", "14", "15"]
CHROME_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android {ver}; {model}) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Mobile Safari/537.36"
)
TIMEZONES = ["Asia/Ho_Chi_Minh", "Asia/Bangkok", "Asia/Singapore",
             "Asia/Tokyo", "Asia/Seoul", "Europe/Berlin", "America/New_York"]
LOCALES = ["vi-VN,vi", "en-US,en", "en-GB,en", "th-TH,th", "id-ID,id", "ja-JP,ja"]

BUILTIN_DEFAULTS = {
    "platform": "android",
    "timezone": "Asia/Ho_Chi_Minh",
    "locale": "vi-VN,vi",
    "proxy": {"mode": "none", "host": "", "port": 0, "username": "", "password": ""},
    "extensions": [],
    "startup_url": "about:blank",
    "webrtc": "default",
    "canvas_noise": True,
    "notes": "",
}


def _pid(n=6):
    alphabet = string.ascii_lowercase + string.digits
    return "p_" + "".join(random.choice(alphabet) for _ in range(n))


def gen_android_fp(seed=None):
    """Sinh fingerprint Android moi. Khong gioi han so luong."""
    rnd = random.Random(seed)
    dev = rnd.choice(ANDROID_DEVICES)
    ver = rnd.choice(ANDROID_VERSIONS)
    major = rnd.randint(126, 134)
    return {
        "platform": "android",
        "device": dev["model"],
        "brand": dev["brand"],
        "os_version": ver,
        "user_agent": CHROME_ANDROID_UA.format(ver=ver, model=dev["model"], major=major),
        "screen": {"width": dev["w"], "height": dev["h"], "dpr": dev["dpr"], "dpi": dev["dpi"]},
        "ram_gb": dev["ram"],
        "timezone": rnd.choice(TIMEZONES),
        "locale": rnd.choice(LOCALES),
        "gpu_vendor": "ARM",
        "gpu_renderer": f"Mali-G{rnd.choice([710, 715, 720, 615])}",
        "canvas_noise": True,
    }


def load_defaults():
    if DEFAULTS_FILE.exists():
        try:
            d = json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))
            out = dict(BUILTIN_DEFAULTS)
            out.update(d)
            return out
        except (OSError, ValueError):
            pass
    return dict(BUILTIN_DEFAULTS)


def save_defaults(d):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULTS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _profile_file(pid):
    return PROFILES_DIR / f"{pid}.json"


def list_profiles():
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(PROFILES_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def get_profile(pid):
    f = _profile_file(pid)
    if not f.exists():
        raise KeyError(pid)
    return json.loads(f.read_text(encoding="utf-8"))


def create_profile(name="", overrides=None):
    """Tao profile moi, ap defaults + fingerprint Android moi. Khong gioi han."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    pid = _pid()
    while _profile_file(pid).exists():
        pid = _pid()
    d = load_defaults()
    fp = gen_android_fp()
    # defaults co the ghi de timezone/locale
    fp["timezone"] = d.get("timezone", fp["timezone"])
    fp["locale"] = d.get("locale", fp["locale"])
    profile = {
        "id": pid,
        "name": name or pid,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fingerprint": fp,
        "proxy": dict(d.get("proxy", BUILTIN_DEFAULTS["proxy"])),
        "extensions": list(d.get("extensions", [])),
        "startup_url": d.get("startup_url", "about:blank"),
        "webrtc": d.get("webrtc", "default"),
        "notes": d.get("notes", ""),
        "user_data_dir": str((DATA_DIR / "user-data" / pid).as_posix()),
    }
    if overrides:
        for k, v in overrides.items():
            if k in profile and k not in ("id", "created_at"):
                profile[k] = v
    _profile_file(pid).write_text(json.dumps(profile, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return profile


def update_profile(pid, patch):
    p = get_profile(pid)
    for k, v in patch.items():
        if k in ("id", "created_at"):
            continue
        p[k] = v
    p["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _profile_file(pid).write_text(json.dumps(p, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return p


def delete_profile(pid, erase_data=True):
    get_profile(pid)  # raise neu khong ton tai
    _profile_file(pid).unlink()
    if erase_data:
        shutil.rmtree(DATA_DIR / "user-data" / pid, ignore_errors=True)
    return True


def rotate_fingerprint(pid):
    p = get_profile(pid)
    tz, loc = p["fingerprint"]["timezone"], p["fingerprint"]["locale"]
    p["fingerprint"] = gen_android_fp()
    p["fingerprint"]["timezone"] = tz
    p["fingerprint"]["locale"] = loc
    p["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _profile_file(pid).write_text(json.dumps(p, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return p


def export_profile(pid, dest):
    p = get_profile(pid)
    Path(dest).write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def import_profile(path):
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    pid = _pid()
    while _profile_file(pid).exists():
        pid = _pid()
    p["id"] = pid
    p.pop("user_data_dir", None)
    p["user_data_dir"] = str((DATA_DIR / "user-data" / pid).as_posix())
    p["imported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _profile_file(pid).write_text(json.dumps(p, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return p


def find_chrome():
    cands = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        r"E:\Orbe\app\1.0.20\chrome\chrome.exe",
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("Khong tim thay Chrome/Chromium tren may")


def launch_profile(pid, chrome_exe=None, extra_args=None):
    """Mo profile bang Chrome voi user-data-dir rieng + UA/proxy. Tra ve Popen."""
    p = get_profile(pid)
    exe = chrome_exe or find_chrome()
    udd = p["user_data_dir"]
    os.makedirs(udd, exist_ok=True)
    fp = p["fingerprint"]
    args = [exe, f"--user-data-dir={udd}", f"--user-agent={fp['user_agent']}",
            f"--lang={fp['locale'].split(',')[0]}"]
    px = p.get("proxy", {})
    if px.get("mode") not in ("none", "", None) and px.get("host") and px.get("port"):
        scheme = px.get("mode", "http")
        args.append(f"--proxy-server={scheme}://{px['host']}:{px['port']}")
    else:
        args.append("--no-proxy-server")
    if fp.get("webrtc") == "disabled" or p.get("webrtc") == "disabled":
        args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
    for ext in p.get("extensions", []):
        if ext and os.path.exists(ext):
            args.append(f"--load-extension={ext}")
    args.append(p.get("startup_url") or "about:blank")
    if extra_args:
        args.extend(extra_args)
    return subprocess.Popen(args)


# ---- Local API (stdlib, cho tool ngoai) ----
class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalApp/1.0"

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/profiles":
            return self._send({"profiles": list_profiles()})
        if self.path.startswith("/profiles/"):
            pid = self.path.rsplit("/", 1)[-1]
            try:
                return self._send(get_profile(pid))
            except KeyError:
                return self._send({"error": "not_found"}, 404)
        if self.path == "/defaults":
            return self._send(load_defaults())
        return self._send({"error": "unknown"}, 404)

    def do_POST(self):
        b = self._body()
        if self.path == "/profiles":
            return self._send(create_profile(b.get("name", ""), b.get("overrides")))
        if self.path.endswith("/launch"):
            pid = self.path.split("/")[2]
            try:
                launch_profile(pid)
                return self._send({"ok": True})
            except (KeyError, FileNotFoundError, OSError) as e:
                return self._send({"error": str(e)}, 500)
        if self.path.endswith("/rotate"):
            pid = self.path.split("/")[2]
            try:
                return self._send(rotate_fingerprint(pid))
            except KeyError:
                return self._send({"error": "not_found"}, 404)
        if self.path == "/profiles/import":
            try:
                return self._send(import_profile(b.get("path", "")))
            except (OSError, ValueError, KeyError) as e:
                return self._send({"error": str(e)}, 400)
        return self._send({"error": "unknown"}, 404)

    def do_PATCH(self):
        if self.path.startswith("/profiles/"):
            pid = self.path.rsplit("/", 1)[-1]
            try:
                return self._send(update_profile(pid, self._body()))
            except KeyError:
                return self._send({"error": "not_found"}, 404)
        if self.path == "/defaults":
            d = load_defaults()
            d.update(self._body())
            save_defaults(d)
            return self._send(d)
        return self._send({"error": "unknown"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/profiles/"):
            pid = self.path.rsplit("/", 1)[-1]
            try:
                delete_profile(pid)
                return self._send({"ok": True})
            except KeyError:
                return self._send({"error": "not_found"}, 404)
        return self._send({"error": "unknown"}, 404)


_api_server = None


def start_api(port=18300):
    global _api_server
    if _api_server:
        return _api_server
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _api_server = srv
    return srv
