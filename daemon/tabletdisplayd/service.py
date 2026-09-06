"""State machine owning the headless output + wayvnc lifecycle.

States: stopped -> running, and an error state reachable from a failed
start. Operations are synchronous and serialized under one lock: every
hyprctl/wayvnc call this daemon makes finishes in well under a second on
real hardware, so modeling separate "starting"/"stopping" states visible to
clients would add complexity without buying responsiveness.
"""

from __future__ import annotations

import json
import threading
import time

from . import config, hypr, netinfo
from .setup_server import SetupServer
from .wayvnc import WayvncSupervisor, WayvncError

STATE_STOPPED = "stopped"
STATE_RUNNING = "running"
STATE_ERROR = "error"


class ServiceError(RuntimeError):
    """Raised for a request that is invalid given the current state."""


class Service:
    def __init__(self, state_dir, runtime_dir, logf=None,
                 default_width=config.DEFAULT_WIDTH,
                 default_height=config.DEFAULT_HEIGHT,
                 default_refresh=config.DEFAULT_REFRESH,
                 vnc_port_preferred=config.DEFAULT_VNC_PORT,
                 setup_port_preferred=config.DEFAULT_SETUP_PORT,
                 setup_page_path=None):
        self.state_dir = state_dir
        self.runtime_dir = runtime_dir
        self._logf = logf or (lambda *a, **k: None)
        self.default_width = default_width
        self.default_height = default_height
        self.default_refresh = default_refresh
        self.vnc_port_preferred = vnc_port_preferred
        self.setup_port_preferred = setup_port_preferred
        self.setup_page_path = setup_page_path or config.setup_page_path()

        self._lock = threading.RLock()
        self.state = STATE_STOPPED
        self.output_name = None
        self.bind_ip = None
        self.width = None
        self.height = None
        self.refresh = None
        self.scale = None
        self.vnc_port = None
        self.setup_port = None
        self.last_error = None
        self._wayvnc: WayvncSupervisor | None = None
        self._setup_server: SetupServer | None = None
        self._on_change = None

    def set_notifier(self, on_change) -> None:
        self._on_change = on_change

    def _session_file(self):
        return self.state_dir / "session.json"

    def _write_session_file(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._session_file().write_text(json.dumps({
            "output_name": self.output_name,
            "created_at": time.time(),
        }))

    def _clear_session_file(self) -> None:
        try:
            self._session_file().unlink()
        except FileNotFoundError:
            pass

    def recover_from_previous_run(self) -> None:
        """Clean up a headless output left behind by an unclean daemon exit.

        A daemon restart (crash + systemd Restart=on-failure, or a manual
        `systemctl --user restart`) loses the in-memory WayvncSupervisor, so
        there is no live process to reattach to -- but the wayvnc *child* it
        started keeps running as an orphan, bound to a control socket only
        this daemon knows the path of. Recovery here means shutting that
        orphan down via its control socket (the same clean request a live
        supervisor would send) and then tearing down whatever stale Hyprland
        output our previous run left active, rather than trying to adopt
        either -- the headless output carries no user data, so discarding it
        is always safe, and a fresh `start` from the client rebuilds it in
        under a second.
        """
        session_file = self._session_file()
        try:
            raw = session_file.read_text()
        except FileNotFoundError:
            return

        try:
            data = json.loads(raw)
            output_name = data.get("output_name")
        except (json.JSONDecodeError, AttributeError):
            output_name = None

        control_socket = str(self.runtime_dir / f"{config.SERVICE_NAME}-wayvnc.sock")
        if WayvncSupervisor.kill_orphan(control_socket):
            self._logf("recovered from unclean shutdown: stopped orphaned wayvnc")

        if output_name:
            try:
                if output_name in hypr.monitor_names():
                    hypr.remove_output(output_name)
                    self._logf("recovered from unclean shutdown: removed stale output %s", output_name)
            except hypr.HyprctlError as exc:
                self._logf("could not clean up stale output %s: %s", output_name, exc)

        self._clear_session_file()

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict:
        client_connected = False
        if self._wayvnc is not None and self.state == STATE_RUNNING:
            client_connected = self._wayvnc.client_count() > 0
        return {
            "state": self.state,
            "output_name": self.output_name,
            "bind_ip": self.bind_ip,
            "vnc_port": self.vnc_port,
            "setup_port": self.setup_port,
            "setup_url": self._setup_server.url if self._setup_server else None,
            "width": self.width,
            "height": self.height,
            "refresh": self.refresh,
            "scale": self.scale,
            "client_connected": client_connected,
            "last_error": self.last_error,
        }

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()

    def start(self, width=None, height=None, refresh=None) -> dict:
        with self._lock:
            if self.state == STATE_RUNNING:
                # Idempotent: a second start while already running is a no-op
                # success, not an error -- a client re-clicking the toggle
                # should never see a failure for state it already has.
                return self._status_locked()

            width = width or self.default_width
            height = height or self.default_height
            refresh = refresh or self.default_refresh

            output_name = None
            supervisor = None
            setup_server = None
            try:
                bind_ip = netinfo.lan_ip()
                vnc_port = netinfo.free_tcp_port(preferred=self.vnc_port_preferred)
                setup_port = netinfo.free_tcp_port(preferred=self.setup_port_preferred)

                output_name = hypr.create_headless_output()
                hypr.set_monitor_mode(output_name, width, height, refresh)

                control_socket = str(self.runtime_dir / f"{config.SERVICE_NAME}-wayvnc.sock")
                supervisor = WayvncSupervisor(output_name, bind_ip, vnc_port, control_socket,
                                               logf=self._logf)
                supervisor.start(on_death=self._notify)

                setup_server = SetupServer(bind_ip, setup_port, self.setup_page_path,
                                            vnc_port=vnc_port,
                                            on_report=self._handle_client_report,
                                            logf=self._logf)
                setup_server.start()
            except (hypr.HyprctlError, WayvncError, netinfo.NoLanAddressError,
                    OSError) as exc:
                if supervisor is not None:
                    supervisor.stop()
                if output_name is not None:
                    try:
                        hypr.remove_output(output_name)
                    except hypr.HyprctlError:
                        pass
                self.state = STATE_ERROR
                self.last_error = str(exc)
                self.output_name = None
                self._wayvnc = None
                self._notify()
                return self._status_locked()

            self.state = STATE_RUNNING
            self.output_name = output_name
            self.bind_ip = bind_ip
            self.vnc_port = vnc_port
            self.setup_port = setup_port
            self.width, self.height, self.refresh = width, height, refresh
            self.scale = 1.0
            self.last_error = None
            self._wayvnc = supervisor
            self._setup_server = setup_server
            self._write_session_file()
            self._notify()
            return self._status_locked()

    def _handle_client_report(self, css_width: int, css_height: int, dpr: float) -> None:
        """Reconfigure the output from the setup page's reported display metrics.

        `css_width`/`css_height` are `screen.width`/`screen.height` as the
        browser reports them -- CSS pixels, already divided by dpr. The
        headless output is set to the tablet's *physical* pixel count
        (css * dpr) with a matching Hyprland scale, so wayvnc captures at
        native resolution while on-screen UI stays a legible logical size.
        """
        physical_width = round(css_width * dpr)
        physical_height = round(css_height * dpr)
        self.set_resolution(physical_width, physical_height, scale=dpr)

    def stop(self) -> dict:
        with self._lock:
            if self.state == STATE_STOPPED:
                return self._status_locked()

            if self._setup_server is not None:
                self._setup_server.stop()
                self._setup_server = None
            if self._wayvnc is not None:
                self._wayvnc.stop()
                self._wayvnc = None
            if self.output_name is not None:
                try:
                    hypr.remove_output(self.output_name)
                except hypr.HyprctlError as exc:
                    self._logf("could not remove output %s during stop: %s", self.output_name, exc)

            self._clear_session_file()
            self.state = STATE_STOPPED
            self.output_name = None
            self.bind_ip = None
            self.vnc_port = None
            self.setup_port = None
            self.width = self.height = self.refresh = self.scale = None
            self.last_error = None
            self._notify()
            return self._status_locked()

    def set_resolution(self, width, height, refresh=None, scale=None) -> dict:
        with self._lock:
            if self.state != STATE_RUNNING:
                raise ServiceError("cannot set resolution while not running")
            refresh = refresh or self.refresh
            scale = scale if scale is not None else (self.scale or 1.0)
            hypr.set_monitor_mode(self.output_name, width, height, refresh, scale=scale)
            self.width, self.height, self.refresh, self.scale = width, height, refresh, scale
            self._notify()
            return self._status_locked()
