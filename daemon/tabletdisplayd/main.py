"""Entrypoint: wire config -> Service -> IPCServer and run until signaled."""

from __future__ import annotations

import signal
import sys
import threading

from . import config
from .ipc_server import IPCServer
from .service import Service


def _log(fmt, *args):
    message = fmt % args if args else fmt
    print(f"kdm-tablet-displayd: {message}", file=sys.stderr, flush=True)


def run() -> int:
    state_dir = config.state_dir()
    runtime_dir = config.runtime_dir()
    socket_path = config.socket_path()

    svc = Service(state_dir=state_dir, runtime_dir=runtime_dir, logf=_log)
    svc.recover_from_previous_run()

    server = IPCServer(socket_path, svc, logf=_log)
    server.start()
    _log("listening on %s", socket_path)

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        _log("received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    stop_event.wait()
    svc.stop()
    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
