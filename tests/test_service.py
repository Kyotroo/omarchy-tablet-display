"""State-machine tests for the daemon's Service, run against fake hyprctl/
wayvnc/wayvncctl/ip fixtures in tests/fixtures/bin (see run.sh), the same
technique kdm.presets uses for its shell helpers: real code paths, a fake
compositor.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "daemon"))

from tabletdisplayd import hypr, netinfo, service  # noqa: E402
from tabletdisplayd.service import Service, ServiceError, STATE_RUNNING, STATE_STOPPED, STATE_ERROR  # noqa: E402


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        # Each test gets its own subtree: fixtures/bin/hyprctl and friends
        # persist state (and append to the invocation log) in plain files,
        # so sharing a directory across tests would leak state between them.
        # A short random suffix (not the full test id) keeps the wayvnc
        # control socket path under AF_UNIX's ~108-byte sun_path limit.
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ["TEST_TMP_DIR"]))
        self.state_dir = self.tmp / "state"
        self.runtime_dir = self.tmp / "runtime"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.hypr_state_path = self.tmp / "hypr-state.json"
        self.hypr_state_path.write_text(json.dumps({
            "monitors": [{"name": "eDP-1", "width": 2880, "height": 1800, "refreshRate": 120.0}],
            "next_headless": 1,
        }))
        os.environ["FAKE_HYPR_STATE"] = str(self.hypr_state_path)
        os.environ["FAKE_LAN_IP"] = "192.168.1.50"
        os.environ.pop("FAKE_HYPR_FAIL", None)
        os.environ.pop("FAKE_WAYVNC_CRASH_AFTER", None)
        os.environ.pop("FAKE_WAYVNC_CRASH_MARKER", None)
        os.environ.pop("FAKE_WAYVNC_CLIENT_COUNT", None)

        self.log_path = self.tmp / "invocations.log"
        os.environ["OMARCHY_TEST_LOG"] = str(self.log_path)

        self.messages = []
        self._services = []

    def tearDown(self):
        for svc in self._services:
            svc.stop()

    def make_service(self, **kwargs):
        svc = Service(state_dir=self.state_dir, runtime_dir=self.runtime_dir,
                       logf=self._capture_log, **kwargs)
        self._services.append(svc)
        return svc

    def _capture_log(self, fmt, *args):
        self.messages.append(fmt % args if args else fmt)

    def hypr_state(self):
        return json.loads(self.hypr_state_path.read_text())

    def log_lines(self):
        if not self.log_path.exists():
            return []
        return self.log_path.read_text().splitlines()

    def _wait_for(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("condition did not become true in time")

    # -- basic lifecycle -------------------------------------------------

    def test_start_creates_headless_output_and_wayvnc(self):
        svc = self.make_service()
        status = svc.start()

        self.assertEqual(status["state"], STATE_RUNNING)
        self.assertEqual(status["output_name"], "HEADLESS-1")
        self.assertEqual(status["width"], service.config.DEFAULT_WIDTH)
        self.assertEqual(status["height"], service.config.DEFAULT_HEIGHT)
        self.assertEqual(status["bind_ip"], "192.168.1.50")

        names = {m["name"] for m in self.hypr_state()["monitors"]}
        self.assertIn("HEADLESS-1", names)
        # The child process needs a brief moment after fork/exec before it
        # gets around to logging its own invocation; svc.start() returning
        # only means Popen() was called, not that the child has run yet.
        self._wait_for(lambda: any(l.startswith("wayvnc ") for l in self.log_lines()))

    def test_start_uses_explicit_resolution(self):
        svc = self.make_service()
        status = svc.start(width=1600, height=900, refresh=48.0)

        self.assertEqual((status["width"], status["height"], status["refresh"]), (1600, 900, 48.0))
        monitor = next(m for m in self.hypr_state()["monitors"] if m["name"] == "HEADLESS-1")
        self.assertEqual((monitor["width"], monitor["height"]), (1600, 900))

    def test_stop_removes_output_and_wayvnc(self):
        svc = self.make_service()
        svc.start()
        status = svc.stop()

        self.assertEqual(status["state"], STATE_STOPPED)
        self.assertIsNone(status["output_name"])
        names = {m["name"] for m in self.hypr_state()["monitors"]}
        self.assertNotIn("HEADLESS-1", names)
        self.assertFalse((self.state_dir / "session.json").exists())

    # -- idempotency -------------------------------------------------------

    def test_double_start_is_a_noop(self):
        svc = self.make_service()
        first = svc.start()
        second = svc.start(width=1234, height=999)  # different args must be ignored

        self.assertEqual(first["output_name"], second["output_name"])
        self.assertEqual(second["width"], first["width"])  # unchanged, not 1234
        # Only one headless output was ever created.
        creates = [l for l in self.log_lines() if l.startswith("hyprctl output create")]
        self.assertEqual(len(creates), 1)

    def test_stop_when_already_stopped_is_a_noop(self):
        svc = self.make_service()
        status = svc.stop()
        self.assertEqual(status["state"], STATE_STOPPED)
        # No hyprctl output/wayvnc calls should have happened at all.
        self.assertEqual(self.log_lines(), [])

    def test_stop_twice_after_start(self):
        svc = self.make_service()
        svc.start()
        svc.stop()
        status = svc.stop()
        self.assertEqual(status["state"], STATE_STOPPED)

    # -- failure handling --------------------------------------------------

    def test_start_failure_leaves_no_orphan_output(self):
        os.environ["FAKE_HYPR_FAIL"] = "eval"
        svc = self.make_service()
        status = svc.start()

        self.assertEqual(status["state"], STATE_ERROR)
        self.assertIsNotNone(status["last_error"])
        # The headless output created before the failing eval call must have
        # been cleaned back up, not left dangling.
        names = {m["name"] for m in self.hypr_state()["monitors"]}
        self.assertNotIn("HEADLESS-1", names)

    def test_start_without_lan_route_fails_before_touching_hyprland(self):
        del os.environ["FAKE_LAN_IP"]
        svc = self.make_service()
        status = svc.start()

        self.assertEqual(status["state"], STATE_ERROR)
        self.assertEqual(self.hypr_state()["monitors"],
                          [{"name": "eDP-1", "width": 2880, "height": 1800, "refreshRate": 120.0}])

    def test_set_resolution_requires_running_state(self):
        svc = self.make_service()
        with self.assertRaises(ServiceError):
            svc.set_resolution(1280, 720)

    # -- crash / recovery ----------------------------------------------------

    def test_wayvnc_respawns_after_unexpected_exit(self):
        os.environ["FAKE_WAYVNC_CRASH_AFTER"] = "0.3"
        os.environ["FAKE_WAYVNC_CRASH_MARKER"] = str(self.tmp / "crashed-once")
        svc = self.make_service()
        svc.start()
        supervisor = svc._wayvnc
        self.assertTrue(supervisor.is_running())

        deadline = time.monotonic() + 5.0
        respawned_and_stable = False
        while time.monotonic() < deadline:
            spawns = [l for l in self.log_lines() if l.startswith("wayvnc ")]
            if len(spawns) >= 2 and supervisor.is_running():
                respawned_and_stable = True
                break
            time.sleep(0.1)

        self.assertTrue(respawned_and_stable, "expected exactly one respawn, then a stable process")
        time.sleep(0.5)  # the crash marker prevents a second crash; confirm it stays up
        self.assertTrue(supervisor.is_running())

    def test_recover_from_previous_run_cleans_stale_output_and_orphan_wayvnc(self):
        # Simulate a crash: a previous Service started, wrote its session
        # file, and the process died with no chance to run stop().
        crashed = self.make_service()
        crashed.start()
        leaked_output = crashed.output_name
        control_socket = crashed._wayvnc.control_socket
        self._wait_for(lambda: os.path.exists(control_socket))
        self._services.remove(crashed)  # do not let tearDown call stop() on it

        # A fresh Service, as main.py constructs on daemon startup.
        recovered = self.make_service()
        recovered.recover_from_previous_run()

        names = {m["name"] for m in self.hypr_state()["monitors"]}
        self.assertNotIn(leaked_output, names)
        self.assertFalse((self.state_dir / "session.json").exists())
        self.assertFalse(os.path.exists(control_socket), "orphaned wayvnc control socket should be gone")
        self.assertTrue(any("recovered from unclean shutdown" in m for m in self.messages))

    def test_recover_with_no_session_file_is_a_noop(self):
        svc = self.make_service()
        svc.recover_from_previous_run()  # must not raise
        self.assertEqual(self.log_lines(), [])

    def test_recover_when_output_already_gone_still_clears_session_file(self):
        crashed = self.make_service()
        crashed.start()
        # The output vanished by some other means (e.g. a full Hyprland
        # restart) before the daemon came back.
        self.hypr_state_path.write_text(json.dumps({
            "monitors": [{"name": "eDP-1", "width": 2880, "height": 1800, "refreshRate": 120.0}],
            "next_headless": 2,
        }))
        crashed._wayvnc.stop()  # the wayvnc side is gone too in this scenario
        self._services.remove(crashed)

        recovered = self.make_service()
        recovered.recover_from_previous_run()
        self.assertFalse((self.state_dir / "session.json").exists())


if __name__ == "__main__":
    unittest.main()
