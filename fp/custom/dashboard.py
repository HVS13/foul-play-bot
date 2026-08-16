import atexit
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from fp.config import FoulPlayConfig
from fp.custom.events import event_snapshot, publish_event

logger = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).resolve().parent.parent / "gui"
_SERVER = None
_THREAD = None


class _DashboardHandler(BaseHTTPRequestHandler):
    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _send_asset(self, filename: str) -> None:
        path = _ASSET_DIR / filename
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "asset unavailable"}, status=500)
            return
        self._send_bytes(200, "text/html; charset=utf-8", body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            self._send_json(event_snapshot())
        elif path == "/api/health":
            self._send_json({"ok": True})
        elif path in {"/", "/index.html"}:
            self._send_asset("dashboard.html")
        elif path in {"/overlay", "/overlay.html"}:
            self._send_asset("overlay.html")
        else:
            self._send_json({"error": "not found"}, status=404)

    def log_message(self, format_string, *args):
        logger.debug("Dashboard: " + format_string, *args)


def stop_dashboard() -> None:
    global _SERVER, _THREAD
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER.server_close()
        _SERVER = None
        _THREAD = None


def start_dashboard(host: str, port: int) -> str:
    global _SERVER, _THREAD
    if _SERVER is not None:
        return "http://{}:{}".format(host, port)

    _SERVER = ThreadingHTTPServer((host, port), _DashboardHandler)
    _THREAD = threading.Thread(
        target=_SERVER.serve_forever,
        name="foul-play-dashboard",
        daemon=True,
    )
    _THREAD.start()
    atexit.register(stop_dashboard)

    url = "http://{}:{}".format(host, port)
    publish_event("dashboard_started", url=url, overlay_url=url + "/overlay")
    logger.info("Dashboard: %s", url)
    logger.info("Overlay: %s/overlay", url)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "Dashboard is listening on a non-loopback address. It has no authentication."
        )
    return url


def maybe_start_dashboard() -> str | None:
    if not getattr(FoulPlayConfig, "gui", False):
        return None
    return start_dashboard(FoulPlayConfig.gui_host, FoulPlayConfig.gui_port)
