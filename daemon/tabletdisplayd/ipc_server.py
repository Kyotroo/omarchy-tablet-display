"""Unix-socket JSON-lines IPC server.

Same shape as hyprmoncfg's IPC: one JSON object per line, request/response
correlated by an `id` the client chooses, plus a `subscribe` method that
opts a connection into unsolicited `status` events pushed on every state
change. A thin client (the QML panel, or a CLI test script) only has to
speak newline-delimited JSON over a Unix socket -- no HTTP, no framing
beyond the newline.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import threading

from . import protocol, service


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        server: IPCServer = self.server.ipc_server  # type: ignore[attr-defined]
        subscribed = False
        try:
            while True:
                try:
                    line = self.rfile.readline()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    # A client going away mid-read (QML plugin reload, daemon
                    # restart racing a live connection) is routine, not a
                    # server fault -- treat it the same as a clean close.
                    break
                if not line:
                    break
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    continue
                response, subscribe_requested = server.dispatch(request)
                if subscribe_requested:
                    subscribed = True
                    server.add_subscriber(self.wfile, self.wfile_lock)
                try:
                    self._write(response)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    break
        finally:
            if subscribed:
                server.remove_subscriber(self.wfile)

    def setup(self):
        super().setup()
        self.wfile_lock = threading.Lock()

    def _write(self, message: dict) -> None:
        with self.wfile_lock:
            self.wfile.write((json.dumps(message) + "\n").encode("utf-8"))
            self.wfile.flush()


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class IPCServer:
    def __init__(self, socket_path, svc: service.Service, logf=None):
        self.socket_path = socket_path
        self.service = svc
        self._logf = logf or (lambda *a, **k: None)
        self._subscribers: list[tuple] = []
        self._subscribers_lock = threading.Lock()
        self._server: _ThreadingUnixServer | None = None
        self._thread: threading.Thread | None = None
        self.service.set_notifier(self._broadcast_status)

    def start(self) -> None:
        self._bind()
        self._server.ipc_server = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        try:
            os.remove(self.socket_path)
        except FileNotFoundError:
            pass

    def _bind(self) -> None:
        path = str(self.socket_path)
        if os.path.exists(path):
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.connect(path)
                probe.close()
                raise RuntimeError(f"IPC socket already in use: {path}")
            except (ConnectionRefusedError, FileNotFoundError):
                os.remove(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._server = _ThreadingUnixServer(path, _Handler)
        os.chmod(path, 0o600)

    def add_subscriber(self, wfile, lock) -> None:
        with self._subscribers_lock:
            self._subscribers.append((wfile, lock))

    def remove_subscriber(self, wfile) -> None:
        with self._subscribers_lock:
            self._subscribers = [s for s in self._subscribers if s[0] is not wfile]

    def _broadcast_status(self) -> None:
        event = protocol.make_event(protocol.EVENT_STATUS, self.service.status())
        payload = (json.dumps(event) + "\n").encode("utf-8")
        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        for wfile, lock in subscribers:
            try:
                with lock:
                    wfile.write(payload)
                    wfile.flush()
            except OSError:
                self.remove_subscriber(wfile)

    def dispatch(self, request: dict) -> tuple[dict, bool]:
        if request.get("type") != "request":
            return protocol.error_response(request, "invalid_request",
                                            "message type must be request"), False

        version = request.get("protocol_version")
        if not isinstance(version, int) or not (
            protocol.MIN_PROTOCOL_VERSION <= version <= protocol.PROTOCOL_VERSION
        ):
            return protocol.error_response(
                request, "unsupported_protocol",
                f"unsupported IPC protocol version {version!r}, this daemon speaks "
                f"{protocol.MIN_PROTOCOL_VERSION} to {protocol.PROTOCOL_VERSION}",
                {"min": protocol.MIN_PROTOCOL_VERSION, "max": protocol.PROTOCOL_VERSION},
            ), False

        method = request.get("method")
        params = request.get("params") or {}
        subscribe_requested = False

        try:
            if method == protocol.METHOD_STATUS:
                result = self.service.status()
            elif method == protocol.METHOD_SUBSCRIBE:
                subscribe_requested = True
                result = self.service.status()
            elif method == protocol.METHOD_START:
                result = self.service.start(
                    width=params.get("width"),
                    height=params.get("height"),
                    refresh=params.get("refresh"),
                )
            elif method == protocol.METHOD_STOP:
                result = self.service.stop()
            elif method == protocol.METHOD_SET_POSITION:
                result = self.service.set_position(params["position"])
            elif method == protocol.METHOD_SET_DISPLAY_MODE:
                result = self.service.set_display_mode(params["mode"])
            elif method == protocol.METHOD_REGENERATE_PASSWORD:
                result = self.service.regenerate_password()
            elif method == protocol.METHOD_SET_PASSWORD:
                result = self.service.set_password(params["password"])
            elif method == protocol.METHOD_SET_USERNAME:
                result = self.service.set_username(params["username"])
            elif method == protocol.METHOD_GET_QR:
                result = {"png_base64": self.service.get_qr_png_base64()}
            elif method == protocol.METHOD_SET_RESOLUTION:
                result = self.service.set_resolution(
                    width=params["width"],
                    height=params["height"],
                    refresh=params.get("refresh"),
                    scale=params.get("scale"),
                )
            else:
                return protocol.error_response(
                    request, "unknown_method", f"unknown IPC method {method!r}"
                ), False
        except service.ServiceError as exc:
            return protocol.error_response(request, "invalid_state", str(exc)), False
        except KeyError as exc:
            return protocol.error_response(request, "invalid_params", f"missing parameter {exc}"), False

        return protocol.make_response(request, result=result), subscribe_requested
