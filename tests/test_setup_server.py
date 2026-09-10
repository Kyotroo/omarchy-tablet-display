"""Tests for the embedded setup-page HTTP server: page serving, QR endpoint,
and the report POST that feeds back into resolution reconfiguration."""
import json
import socket
import struct
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "daemon"))

from tabletdisplayd.setup_server import SetupServer  # noqa: E402

PAGE_PATH = REPO_ROOT / "daemon" / "setup_page" / "index.html"
TOKEN = "test-token-Ab12"


class SetupServerTestCase(unittest.TestCase):
    def setUp(self):
        self.reports = []
        self.server = SetupServer(
            "127.0.0.1", 0, PAGE_PATH, vnc_port=5900,
            on_report=lambda w, h, d: self.reports.append((w, h, d)),
            vnc_username="someone", vnc_password="Ab3dEfGh9k",
            setup_token=TOKEN,
        )
        # Port 0 means "any free port" for a raw socket, but ThreadingHTTPServer
        # needs a concrete one up front -- resolve one the same way the daemon
        # does and use it explicitly.
        sys.path.insert(0, str(REPO_ROOT / "daemon"))
        from tabletdisplayd import netinfo
        self.server.port = netinfo.free_tcp_port()
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()

    def get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=3)

    def post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                      headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=3)

    def post_report(self, width, height, dpr=None, token=TOKEN):
        payload = {"token": token, "width": width, "height": height}
        if dpr is not None:
            payload["dpr"] = dpr
        return self.post("/setup/report", payload)

    def test_setup_page_served_with_port_substituted(self):
        with self.get("/setup") as res:
            body = res.read().decode()
        self.assertEqual(res.status, 200)
        self.assertIn("5900", body)
        self.assertNotIn("__VNC_PORT__", body)

    def test_setup_page_shows_username_and_password(self):
        with self.get("/setup") as res:
            body = res.read().decode()
        self.assertIn("Ab3dEfGh9k", body)
        self.assertIn("someone", body)
        self.assertNotIn("__VNC_PASSWORD__", body)
        self.assertNotIn("__VNC_USERNAME__", body)

    def test_setup_page_embeds_the_setup_token(self):
        with self.get("/setup") as res:
            body = res.read().decode()
        self.assertIn(TOKEN, body)
        self.assertNotIn("__SETUP_TOKEN__", body)

    def test_setup_url_includes_the_token(self):
        self.assertIn(f"t={TOKEN}", self.server.url)

    def test_the_actual_setup_url_this_server_hands_out_is_fetchable(self):
        # server.url includes the query string (?t=...); GET must route on
        # the path alone or the exact link this daemon puts in the QR code
        # would 404. Caught live: an earlier version of do_GET compared
        # self.path to "/setup" exactly, before the token was added to the
        # URL at all.
        path_and_query = self.server.url.split("/setup", 1)[1]
        with self.get("/setup" + path_and_query) as res:
            self.assertEqual(res.status, 200)

    def test_qr_png_served(self):
        with self.get("/qr.png") as res:
            body = res.read()
        self.assertEqual(res.status, 200)
        self.assertEqual(res.headers["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_abrupt_client_disconnect_does_not_take_down_the_server(self):
        # A mobile browser backgrounding mid-request (or a QR-scanner app's
        # preview fetch) resets the connection rather than closing it
        # cleanly. SO_LINGER(0) forces the kernel to send an RST on close,
        # which is what surfaces as ConnectionResetError server-side.
        rude = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        rude.connect(("127.0.0.1", self.server.port))
        rude.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        rude.send(b"GET /setup HTTP/1.1\r\n")  # deliberately incomplete request
        rude.close()

        time.sleep(0.2)

        with self.get("/qr.png") as res:
            self.assertEqual(res.status, 200)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_valid_report_invokes_callback(self):
        with self.post_report(1600, 900, 2.0) as res:
            self.assertEqual(res.status, 204)
        self.assertEqual(self.reports, [(1600, 900, 2.0)])

    def test_report_defaults_dpr_to_one(self):
        with self.post_report(1280, 800) as res:
            self.assertEqual(res.status, 204)
        self.assertEqual(self.reports, [(1280, 800, 1.0)])

    def test_report_missing_token_is_rejected(self):
        # A security review found this endpoint accepted a POST from any
        # LAN peer, not just whoever loaded this session's own setup page.
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/setup/report", {"width": 1280, "height": 800})
        self.assertEqual(ctx.exception.code, 400)  # missing key -> malformed request
        self.assertEqual(self.reports, [])

    def test_report_wrong_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post_report(1280, 800, token="not-the-right-token")
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(self.reports, [])

    def test_report_rejects_out_of_range_dimensions(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post_report(0, 900, 1)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.reports, [])

    def test_report_rejects_dimensions_above_the_configured_ceiling(self):
        # The old ceiling (16000 per axis, DPR up to 8) let an
        # unauthenticated peer force a 128000x128000 request -- this is the
        # HTTP-layer half of that fix; the physical (post-DPR) product is
        # bounded again in service.py regardless of what combination of
        # values produced it.
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post_report(16000, 900, 8)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.reports, [])

    def test_report_rejects_malformed_json(self):
        req = urllib.request.Request(
            self.base + "/setup/report", data=b"not json", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 400)

    def test_report_callback_exception_does_not_break_the_server(self):
        from tabletdisplayd import netinfo

        def blow_up(w, h, d):
            raise RuntimeError("simulated failure applying resolution")

        flaky = SetupServer("127.0.0.1", netinfo.free_tcp_port(), PAGE_PATH,
                             vnc_port=5900, on_report=blow_up,
                             vnc_username="someone", vnc_password="Ab3dEfGh9k",
                             setup_token=TOKEN)
        flaky.start()
        try:
            base = f"http://127.0.0.1:{flaky.port}"
            data = json.dumps({"token": TOKEN, "width": 1024, "height": 768, "dpr": 1}).encode()
            req = urllib.request.Request(base + "/setup/report", data=data, method="POST",
                                          headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as res:
                self.assertEqual(res.status, 204)
            # The server itself must still be serving afterward.
            with urllib.request.urlopen(base + "/qr.png", timeout=3) as res:
                self.assertEqual(res.status, 200)
        finally:
            flaky.stop()


if __name__ == "__main__":
    unittest.main()
