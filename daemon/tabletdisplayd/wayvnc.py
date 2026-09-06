"""Supervises a single wayvnc subprocess bound to one output.

Community reports (and the goal spec) call out wayvnc sessions dying roughly
30s after the physical screen locks/DPMS-off, requiring a manual restart.
This supervisor treats an unexpected wayvnc exit while the daemon still wants
the session running as a transient fault: it respawns with a short backoff
rather than surfacing it as a hard failure, so a DPMS-triggered death repairs
itself without the user noticing.
"""

from __future__ import annotations

import subprocess
import threading
import time

WAYVNC = "wayvnc"
WAYVNCCTL = "wayvncctl"

RESPAWN_BACKOFF_SECONDS = (1, 2, 5, 10)
RESPAWN_RESET_AFTER_SECONDS = 30


class WayvncError(RuntimeError):
    pass


class WayvncSupervisor:
    def __init__(self, output_name: str, bind_ip: str, port: int, control_socket: str,
                 logf=None, config_path: str | None = None):
        self.output_name = output_name
        self.bind_ip = bind_ip
        self.port = port
        self.control_socket = control_socket
        self.config_path = config_path
        self._logf = logf or (lambda *a, **k: None)

        self._proc: subprocess.Popen | None = None
        self._watch_thread: threading.Thread | None = None
        self._want_running = False
        self._lock = threading.Lock()
        self._respawn_count = 0
        self._last_start = 0.0
        self._on_death = None

    def start(self, on_death=None) -> None:
        """Launch wayvnc and begin supervising it.

        `on_death` is called (from the watcher thread) whenever the process
        exits while still wanted -- after this supervisor has already
        respawned it -- so callers can push a status event without polling.
        """
        with self._lock:
            if self._proc is not None:
                raise WayvncError("wayvnc is already running")
            self._want_running = True
            self._on_death = on_death
            self._respawn_count = 0
            self._spawn_locked()
            self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self._watch_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._want_running = False
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
        thread = self._watch_thread
        self._watch_thread = None
        if thread is not None:
            thread.join(timeout=timeout)

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def _spawn_locked(self) -> None:
        address = f"{self.bind_ip}:{self.port}"
        command = [WAYVNC, "--output", self.output_name, "--socket", self.control_socket]
        if self.config_path:
            command += ["--config", self.config_path]
        command.append(address)
        self._proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._last_start = time.monotonic()

    def _watch_loop(self) -> None:
        while True:
            with self._lock:
                proc = self._proc
                want = self._want_running
            if proc is None or not want:
                return
            proc.wait()
            with self._lock:
                if not self._want_running or self._proc is not proc:
                    # stop() already tore this down, or a newer process took over.
                    return
                self._proc = None

                if time.monotonic() - self._last_start > RESPAWN_RESET_AFTER_SECONDS:
                    self._respawn_count = 0
                backoff_index = min(self._respawn_count, len(RESPAWN_BACKOFF_SECONDS) - 1)
                delay = RESPAWN_BACKOFF_SECONDS[backoff_index]
                self._respawn_count += 1

            self._logf(
                "wayvnc exited unexpectedly (code=%s); respawning in %ss",
                proc.returncode, delay,
            )
            time.sleep(delay)

            with self._lock:
                if not self._want_running:
                    return
                self._spawn_locked()

            if self._on_death:
                self._on_death()

    @staticmethod
    def kill_orphan(control_socket: str) -> bool:
        """Best-effort shutdown of a wayvnc process from a previous daemon run.

        A daemon crash (kill -9, OOM, etc.) leaves its child wayvnc running,
        bound to a control socket only this daemon knows about. `wayvnc-exit`
        over that socket is the same clean shutdown a live supervisor would
        request; a dead/unresponsive socket means there was nothing to kill.
        Returns True if a live wayvnc actually responded to the exit request.
        """
        try:
            result = subprocess.run(
                [WAYVNCCTL, "--socket", control_socket, "wayvnc-exit"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def client_count(self) -> int:
        """Best-effort count of connected VNC sessions via wayvncctl."""
        try:
            result = subprocess.run(
                [WAYVNCCTL, "--socket", self.control_socket, "--json", "client-list"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0
        if result.returncode != 0:
            return 0
        import json
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return 0
        if isinstance(data, list):
            return len(data)
        return 0
