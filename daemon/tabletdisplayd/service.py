"""State machine owning the headless output + wayvnc lifecycle.

States: stopped -> running, and an error state reachable from a failed
start. Operations are synchronous and serialized under one lock: every
hyprctl/wayvnc call this daemon makes finishes in well under a second on
real hardware, so modeling separate "starting"/"stopping" states visible to
clients would add complexity without buying responsiveness.
"""

from __future__ import annotations

import base64
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
        # True when output_name is a headless output this daemon created
        # (extend mode) and therefore owns and must remove on stop; False
        # when it is a real physical/existing monitor being captured
        # in-place (mirror mode) that must never be touched by remove_output.
        self._owns_output = False
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

        # Unlike width/height/refresh/scale (the live session's own numbers,
        # meaningless once stopped), position and display_mode are standing
        # user preferences: they survive stop/start and are loaded once here
        # rather than reset to a hardcoded default every session.
        settings = self._load_settings()
        self.position = settings.get("position", config.DEFAULT_POSITION)
        self.display_mode = settings.get("display_mode", config.DEFAULT_DISPLAY_MODE)
        self.mirror_source = None

    def set_notifier(self, on_change) -> None:
        self._on_change = on_change

    def _settings_file(self):
        return self.state_dir / "settings.json"

    def _load_settings(self) -> dict:
        try:
            return json.loads(self._settings_file().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._settings_file().write_text(json.dumps({
            "position": self.position,
            "display_mode": self.display_mode,
        }))

    def _session_file(self):
        return self.state_dir / "session.json"

    def _write_session_file(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._session_file().write_text(json.dumps({
            "output_name": self.output_name,
            "owns_output": self._owns_output,
            "created_at": time.time(),
        }))

    def _clear_session_file(self) -> None:
        try:
            self._session_file().unlink()
        except FileNotFoundError:
            pass

    def recover_from_previous_run(self) -> None:
        """Clean up state left behind by an unclean daemon exit.

        A daemon restart (crash + systemd Restart=on-failure, or a manual
        `systemctl --user restart`) loses the in-memory WayvncSupervisor, so
        there is no live process to reattach to -- but the wayvnc *child* it
        started keeps running as an orphan, bound to a control socket only
        this daemon knows the path of. Recovery here means shutting that
        orphan down via its control socket (the same clean request a live
        supervisor would send) and then tearing down whatever stale headless
        output our previous run created, rather than trying to adopt either.

        Critically: only ever removes the output if the session file says
        this daemon *owns* it (a headless output it created for extend
        mode). Mirror mode captures a real physical monitor directly with
        no headless output at all -- output_name in that case is something
        like "eDP-1", and calling hyprctl output remove on it would attempt
        to remove a real monitor, not clean up plugin state. A session file
        from before this distinction existed has no "owns_output" key, and
        every one of those was necessarily a headless output (mirror mode
        used to also create one, just uselessly), so a missing key defaults
        to True rather than skipping cleanup of genuinely old state.
        """
        session_file = self._session_file()
        try:
            raw = session_file.read_text()
        except FileNotFoundError:
            return

        try:
            data = json.loads(raw)
            output_name = data.get("output_name")
            owns_output = data.get("owns_output", True)
        except (json.JSONDecodeError, AttributeError):
            output_name = None
            owns_output = False

        control_socket = str(self.runtime_dir / f"{config.SERVICE_NAME}-wayvnc.sock")
        if WayvncSupervisor.kill_orphan(control_socket):
            self._logf("recovered from unclean shutdown: stopped orphaned wayvnc")

        if output_name and owns_output:
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
            "qr_url": self._setup_server.qr_url if self._setup_server else None,
            "width": self.width,
            "height": self.height,
            "refresh": self.refresh,
            "scale": self.scale,
            "position": self.position,
            "display_mode": self.display_mode,
            "mirror_source": self.mirror_source,
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
            owns_output = False
            supervisor = None
            setup_server = None
            try:
                bind_ip = netinfo.lan_ip()
                vnc_port = netinfo.free_tcp_port(preferred=self.vnc_port_preferred)
                setup_port = netinfo.free_tcp_port(preferred=self.setup_port_preferred)

                if self.display_mode == "mirror":
                    # A Hyprland `mirror` output is a hardware/DRM-level
                    # clone with no independent Wayland surface of its own
                    # -- confirmed live with `wayvnc -v`, which never lists
                    # a mirrored headless output as a capturable target at
                    # all, only real ones. So duplicating the screen means
                    # pointing wayvnc directly at the real monitor, not
                    # creating a virtual output and trying to capture that.
                    source = hypr.focused_monitor_name()
                    if not source:
                        raise hypr.HyprctlError("no monitor is currently focused to duplicate")
                    source_monitor = next(
                        (m for m in hypr.monitors() if m["name"] == source), None
                    )
                    if source_monitor is None:
                        raise hypr.HyprctlError(f"could not read the mode of monitor {source!r}")
                    output_name = source
                    owns_output = False
                    width = source_monitor["width"]
                    height = source_monitor["height"]
                    refresh = source_monitor.get("refreshRate")
                    scale_applied = source_monitor.get("scale", 1.0)
                    mirror_source = source
                else:
                    output_name = hypr.create_headless_output()
                    owns_output = True
                    hypr.set_monitor_mode(output_name, width, height, refresh, scale=1.0,
                                           position=self.position)
                    scale_applied = 1.0
                    mirror_source = None

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
                if output_name is not None and owns_output:
                    try:
                        hypr.remove_output(output_name)
                    except hypr.HyprctlError:
                        pass
                self.state = STATE_ERROR
                self.last_error = str(exc)
                self.output_name = None
                self._owns_output = False
                self._wayvnc = None
                self._notify()
                return self._status_locked()

            self.state = STATE_RUNNING
            self.output_name = output_name
            self._owns_output = owns_output
            self.bind_ip = bind_ip
            self.vnc_port = vnc_port
            self.setup_port = setup_port
            self.width, self.height, self.refresh = width, height, refresh
            self.scale = scale_applied
            self.mirror_source = mirror_source
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

        A no-op while mirroring: the mirrored output's resolution is
        dictated by its source, not by the tablet, and this report is a
        passive side effect of the tablet merely loading the setup page --
        not something that should surface as an error to the user.
        """
        if self.display_mode == "mirror":
            return
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
            if self.output_name is not None and self._owns_output:
                try:
                    hypr.remove_output(self.output_name)
                except hypr.HyprctlError as exc:
                    self._logf("could not remove output %s during stop: %s", self.output_name, exc)

            self._clear_session_file()
            self.state = STATE_STOPPED
            self.output_name = None
            self._owns_output = False
            self.bind_ip = None
            self.vnc_port = None
            self.setup_port = None
            self.width = self.height = self.refresh = self.scale = None
            self.mirror_source = None
            self.last_error = None
            self._notify()
            return self._status_locked()

    def get_qr_png_base64(self) -> str:
        """Base64 PNG for the panel's QR image.

        Delivered over the same Unix socket as everything else rather than
        as an http:// Image source: nothing else in this shell loads an
        Image from a network URL, and testing confirmed QtQuick's Image
        element never resolves one here (stays in Loading forever, no
        error) -- Quickshell's plugin QML has no wired-up network image
        loader. A data: URI needs no network stack at all.
        """
        with self._lock:
            if self.state != STATE_RUNNING or self._setup_server is None:
                raise ServiceError("cannot fetch QR code while not running")
            return base64.b64encode(self._setup_server.qr_png_bytes).decode("ascii")

    def set_resolution(self, width, height, refresh=None, scale=None) -> dict:
        with self._lock:
            if self.state != STATE_RUNNING:
                raise ServiceError("cannot set resolution while not running")
            if self.display_mode == "mirror":
                raise ServiceError("cannot set resolution while mirroring another screen")
            refresh = refresh or self.refresh
            scale = scale if scale is not None else (self.scale or 1.0)
            hypr.set_monitor_mode(self.output_name, width, height, refresh, scale=scale,
                                   position=self.position)
            self.width, self.height, self.refresh, self.scale = width, height, refresh, scale
            self._notify()
            return self._status_locked()

    def set_position(self, position: str) -> dict:
        with self._lock:
            if position not in config.VALID_POSITIONS:
                raise ServiceError(
                    f"invalid position {position!r}, must be one of {config.VALID_POSITIONS}"
                )
            self.position = position
            self._save_settings()
            # Position is meaningless while mirroring: there is no virtual
            # output to position at all in that mode (output_name is the
            # real physical monitor being captured directly), so applying
            # this would reposition a real monitor. Persist the preference
            # either way; it takes effect on the next switch to extend mode.
            if self.state == STATE_RUNNING and self.display_mode == "extend":
                hypr.set_monitor_mode(self.output_name, self.width, self.height, self.refresh,
                                       scale=self.scale, position=position)
            self._notify()
            return self._status_locked()

    def set_display_mode(self, mode: str) -> dict:
        with self._lock:
            if mode not in config.VALID_DISPLAY_MODES:
                raise ServiceError(
                    f"invalid display mode {mode!r}, must be one of {config.VALID_DISPLAY_MODES}"
                )
            was_running = self.state == STATE_RUNNING
            # Extend and mirror capture fundamentally different things (a
            # headless output this daemon owns vs. a real monitor captured
            # in-place), so there is no in-place reapply between them the
            # way changing resolution or position has -- a full stop/start
            # cycle is the only way to switch cleanly. The VNC session
            # briefly drops and the client reconnects; that is an honest
            # reflection of what is actually changing, not a shortcut.
            prior_width, prior_height, prior_refresh = self.width, self.height, self.refresh

            self.display_mode = mode
            self._save_settings()

            if was_running:
                self.stop()
                status = self.start(width=prior_width, height=prior_height, refresh=prior_refresh)
                if status["state"] == STATE_ERROR:
                    raise ServiceError(status["last_error"] or "failed to switch display mode")
                return status

            self._notify()
            return self._status_locked()
