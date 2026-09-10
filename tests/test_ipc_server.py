"""Protocol-level tests for the IPC server: request/response framing,
version negotiation, unknown methods, and the subscribe/broadcast path a
real Quickshell `Socket{parser: SplitParser{splitMarker:"\\n"}}` client
depends on.
"""
import json
import os
import socket
import struct
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "daemon"))

from tabletdisplayd import protocol  # noqa: E402
from tabletdisplayd.ipc_server import IPCServer  # noqa: E402
from tabletdisplayd.service import Service  # noqa: E402


def request(method, params=None):
    return {
        "type": "request",
        "protocol_version": 1,
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params or {},
    }


class DispatchTestCase(unittest.TestCase):
    def setUp(self):
        # A fresh subdir per test: settings.json (position/display_mode/
        # username/password) persists across Service instances by design,
        # so tests sharing one directory would leak state between each
        # other -- confirmed live. Same fix ServiceTestCase already uses.
        tmp = Path(tempfile.mkdtemp(dir=os.environ["TEST_TMP_DIR"]))
        self.svc = Service(state_dir=tmp / "state2", runtime_dir=tmp / "runtime2")
        self.server = IPCServer(tmp / "unused.sock", self.svc)

    def test_status_dispatch(self):
        response, subscribe = self.server.dispatch(request(protocol.METHOD_STATUS))
        self.assertFalse(subscribe)
        self.assertEqual(response["result"]["state"], "stopped")
        self.assertNotIn("error", response)

    def test_unknown_method(self):
        response, _ = self.server.dispatch(request("levitate"))
        self.assertEqual(response["error"]["code"], "unknown_method")

    def test_wrong_message_type_rejected(self):
        bad = request(protocol.METHOD_STATUS)
        bad["type"] = "notification"
        response, _ = self.server.dispatch(bad)
        self.assertEqual(response["error"]["code"], "invalid_request")

    def test_unsupported_protocol_version(self):
        bad = request(protocol.METHOD_STATUS)
        bad["protocol_version"] = 99
        response, _ = self.server.dispatch(bad)
        self.assertEqual(response["error"]["code"], "unsupported_protocol")

    def test_subscribe_flagged_and_returns_status(self):
        response, subscribe = self.server.dispatch(request(protocol.METHOD_SUBSCRIBE))
        self.assertTrue(subscribe)
        self.assertEqual(response["result"]["state"], "stopped")

    def test_get_qr_while_stopped_is_invalid_state_error(self):
        response, _ = self.server.dispatch(request(protocol.METHOD_GET_QR))
        self.assertEqual(response["error"]["code"], "invalid_state")

    def test_set_position_missing_params_is_invalid_params_error(self):
        response, _ = self.server.dispatch(request(protocol.METHOD_SET_POSITION, {}))
        self.assertEqual(response["error"]["code"], "invalid_params")

    def test_set_position_valid_value_while_stopped_succeeds(self):
        response, _ = self.server.dispatch(request(protocol.METHOD_SET_POSITION, {"position": "auto-up"}))
        self.assertNotIn("error", response)
        self.assertEqual(response["result"]["position"], "auto-up")

    def test_regenerate_password_while_stopped_succeeds(self):
        response, _ = self.server.dispatch(request(protocol.METHOD_REGENERATE_PASSWORD))
        self.assertNotIn("error", response)

    def test_set_username_missing_params_is_invalid_params_error(self):
        response, _ = self.server.dispatch(request(protocol.METHOD_SET_USERNAME, {}))
        self.assertEqual(response["error"]["code"], "invalid_params")

    def test_set_username_valid_value_succeeds(self):
        response, _ = self.server.dispatch(request(protocol.METHOD_SET_USERNAME, {"username": "kdm"}))
        self.assertNotIn("error", response)
        self.assertEqual(response["result"]["vnc_username"], "kdm")

    def test_set_resolution_missing_params_is_invalid_params_error(self):
        response, _ = self.server.dispatch(request(protocol.METHOD_SET_RESOLUTION, {}))
        self.assertEqual(response["error"]["code"], "invalid_params")

    def test_response_echoes_request_id(self):
        req = request(protocol.METHOD_STATUS)
        response, _ = self.server.dispatch(req)
        self.assertEqual(response["id"], req["id"])


class SocketWireTestCase(unittest.TestCase):
    """End-to-end over a real Unix socket: proves the newline-delimited JSON
    framing a Quickshell SplitParser("\\n") client relies on actually holds."""

    def setUp(self):
        tmp = Path(os.environ["TEST_TMP_DIR"])
        self.socket_path = tmp / "wire-test.sock"
        self.svc = Service(state_dir=tmp / "state3", runtime_dir=tmp / "runtime3")
        self.server = IPCServer(self.socket_path, self.svc)
        self.server.start()
        time.sleep(0.05)

    def tearDown(self):
        self.server.stop()

    def _send(self, sock, req):
        sock.sendall((json.dumps(req) + "\n").encode())

    def _recv_line(self, sock):
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.split(b"\n", 1)[0])

    def test_request_response_round_trip(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(self.socket_path))
            self._send(sock, request(protocol.METHOD_STATUS))
            response = self._recv_line(sock)
        self.assertEqual(response["result"]["state"], "stopped")

    def test_subscribed_client_receives_status_event_on_change(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(self.socket_path))
            self._send(sock, request(protocol.METHOD_SUBSCRIBE))
            self._recv_line(sock)  # the subscribe response itself

            self.svc._notify()  # simulate an internal state change

            event = self._recv_line(sock)
            self.assertEqual(event["type"], "event")
            self.assertEqual(event["event"], protocol.EVENT_STATUS)

    def test_abrupt_client_disconnect_does_not_take_down_the_server(self):
        # A QML plugin hot-reload or a client machine dropping off mid-read
        # resets the connection rather than closing it cleanly. SO_LINGER(0)
        # forces the kernel to send an RST on close instead of a FIN, which
        # is what surfaces as ConnectionResetError server-side.
        rude = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        rude.connect(str(self.socket_path))
        rude.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        rude.send(b'{"type": "request"')  # a deliberately incomplete line
        rude.close()

        time.sleep(0.2)  # let the server-side thread observe the reset

        # The server must still be alive and answering other clients.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(self.socket_path))
            self._send(sock, request(protocol.METHOD_STATUS))
            response = self._recv_line(sock)
        self.assertEqual(response["result"]["state"], "stopped")

    def test_multiple_clients_can_connect_concurrently(self):
        sockets = []
        try:
            for _ in range(3):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(str(self.socket_path))
                sockets.append(s)
            for s in sockets:
                self._send(s, request(protocol.METHOD_STATUS))
            for s in sockets:
                response = self._recv_line(s)
                self.assertEqual(response["result"]["state"], "stopped")
        finally:
            for s in sockets:
                s.close()


if __name__ == "__main__":
    unittest.main()
