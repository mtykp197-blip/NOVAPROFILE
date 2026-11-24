"""Backend cuc bo cho app desktop Electron.

Electron chay file nay (hoac ban dong goi ``nova-backend.exe``), doc
dong dau tien tren stdout de biet cong va token, roi goi
``POST /rpc/<method>`` voi than JSON la danh sach tham so.

Chi lang nghe tren 127.0.0.1 va moi yeu cau phai mang token ngau nhien,
nen mot trang web hay mot tien trinh khac tren may khong goi duoc.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .bridge import Bridge

ALLOWED = {
    "list_profiles",
    "get_profile",
    "get_defaults",
    "save_defaults",
    "stats",
    "create_profiles",
    "update_profile",
    "rotate_fingerprint",
    "delete_profiles",
    "open_profile",
    "close_profile",
}

MAX_BODY = 1_000_000


def make_handler(bridge: Bridge, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # khong in log ra stdout
            return

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            supplied = self.headers.get("X-Nova-Token", "")
            if not hmac.compare_digest(supplied, token):
                self._send(403, {"ok": False, "error": "forbidden"})
                return
            if not self.path.startswith("/rpc/"):
                self._send(404, {"ok": False, "error": "not found"})
                return
            method = self.path[len("/rpc/"):]
            if method not in ALLOWED:
                self._send(404, {"ok": False, "error": "unknown method"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 0 or length > MAX_BODY:
                self._send(413, {"ok": False, "error": "body too large"})
                return
            try:
                raw = self.rfile.read(length) if length else b"[]"
                args = json.loads(raw.decode("utf-8") or "[]")
                if not isinstance(args, list):
                    raise ValueError("args must be a list")
            except ValueError as exc:
                self._send(400, {"ok": False, "error": f"bad request: {exc}"})
                return
            try:
                result = getattr(bridge, method)(*args)
            except Exception as exc:  # loi engine tra ve giao dien, khong lam sap server
                self._send(200, {"ok": False, "error": str(exc)})
                return
            self._send(200, {"ok": True, "result": result})

    return Handler


def serve(port: int = 0) -> None:
    token = secrets.token_hex(24)
    bridge = Bridge()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(bridge, token))
    actual = server.server_address[1]
    sys.stdout.write(json.dumps({"port": actual, "token": token}) + "\n")
    sys.stdout.flush()

    # Electron dong stdin khi thoat -> backend tu tat, khong de tien trinh mo coi.
    def _watch_parent() -> None:
        try:
            sys.stdin.read()
        except Exception:
            pass
        server.shutdown()

    threading.Thread(target=_watch_parent, daemon=True).start()
    server.serve_forever()


def main() -> int:
    if getattr(sys, "frozen", False):
        import multiprocessing

        multiprocessing.freeze_support()
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
