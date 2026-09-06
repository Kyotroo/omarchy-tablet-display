"""Embedded HTTP server that serves the tablet-facing setup page.

Runs on the same LAN address as wayvnc, on its own port. The page is a
single static file with inline JS (no build step); its detect -> report ->
redirect flow is described in daemon/setup_page/index.html. This module is
only the plumbing: serve the page, serve a QR code of its own URL, and
accept the one POST the page makes back with the device's reported display
metrics.
"""

from __future__ import annotations

import http.server
import json
import sys
import threading

from . import qrcode_gen

MAX_REPORT_BODY = 8192


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        self.server.tabletdisplay_logf("setup-http: " + fmt, *args)

    def do_GET(self):
        if self.path in ("/setup", "/setup/"):
            self.server.tabletdisplay_logf(
                "setup-http: GET /setup from %s, User-Agent: %s",
                self.client_address[0], self.headers.get("User-Agent", "(none)"),
            )
            self._send(200, "text/html; charset=utf-8", self.server.tabletdisplay_page)
        elif self.path == "/qr.png":
            self._send(200, "image/png", self.server.tabletdisplay_qr_png)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if self.path != "/setup/report":
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_REPORT_BODY:
            self._send(400, "text/plain; charset=utf-8", b"bad request")
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            width = int(payload["width"])
            height = int(payload["height"])
            dpr = float(payload.get("dpr", 1))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._send(400, "text/plain; charset=utf-8", b"bad request")
            return

        # The reported dimensions are the only thing this daemon trusts from
        # the client; a user agent string is display-only on the page and
        # never reaches here. Bounds are generous but reject garbage/hostile
        # values (e.g. a 0 or a many-million-pixel claim).
        if not (1 <= width <= 16000 and 1 <= height <= 16000 and 0.25 <= dpr <= 8):
            self._send(400, "text/plain; charset=utf-8", b"value out of range")
            return

        try:
            self.server.tabletdisplay_on_report(width, height, dpr)
        except Exception as exc:  # noqa: BLE001 -- a bad report must not take the server down
            self.server.tabletdisplay_logf("could not apply reported resolution: %s", exc)

        self._send(204, None, b"")

    def _send(self, status, content_type, body):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


class _ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # A mobile browser aborting a connection mid-request (backgrounded,
        # navigated away, or just an early QR-scanner preview fetch) resets
        # the socket -- routine, not a server fault. Anything else still
        # gets the normal traceback.
        exc_type = sys.exc_info()[0]
        if exc_type in (ConnectionResetError, BrokenPipeError):
            return
        super().handle_error(request, client_address)


class SetupServer:
    def __init__(self, bind_ip: str, port: int, page_path, vnc_port: int, on_report,
                 vnc_username: str | None = None, vnc_password: str | None = None, logf=None):
        self.bind_ip = bind_ip
        self.port = port
        self.page_path = page_path
        self.vnc_port = vnc_port
        self.vnc_username = vnc_username
        self.vnc_password = vnc_password
        self._on_report = on_report
        self._logf = logf or (lambda *a, **k: None)
        self._server: _ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.qr_png_bytes: bytes = b""

    @property
    def url(self) -> str:
        return f"http://{self.bind_ip}:{self.port}/setup"

    @property
    def qr_url(self) -> str:
        return f"http://{self.bind_ip}:{self.port}/qr.png"

    def start(self) -> None:
        # The page has no template engine of its own; the wayvnc port is
        # only known once this session has started, so it is substituted in
        # at serve time rather than templated ahead of time.
        page_text = self.page_path.read_text(encoding="utf-8")
        page_text = page_text.replace("__VNC_PORT__", str(self.vnc_port))
        page_text = page_text.replace("__VNC_PASSWORD__", self.vnc_password or "")
        page_text = page_text.replace("__VNC_USERNAME__", self.vnc_username or "")
        page_bytes = page_text.encode("utf-8")
        qr_png = qrcode_gen.generate_png(self.url)
        self.qr_png_bytes = qr_png

        self._server = _ThreadingHTTPServer((self.bind_ip, self.port), _Handler)
        self._server.tabletdisplay_page = page_bytes
        self._server.tabletdisplay_qr_png = qr_png
        self._server.tabletdisplay_on_report = self._on_report
        self._server.tabletdisplay_logf = self._logf

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
