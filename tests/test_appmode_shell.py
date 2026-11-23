"""App mode shell test (test-first, failing until built).

Problem in one sentence: there is no window yet, so a consumer
cannot double-click Iris, watch Runs, and stop one, all in one place.

Success criteria for this test Run (human-approved, once per task
type, as a named read-back, before the Run starts):
  task_type = "app mode shell"
  clause    = "one window shows the loopback panel; Verify, Pause,
               and Emergency stop are native buttons outside the web
               view; the proxy starts with the app and stops when the
               window closes; a second start refuses in plain words"
  read_back = "named read-back: window opens on the loopback panel;
               native buttons call the seams; close stops the proxy;
               second start refuses, no second proxy"
No approved done = Unverified, never Correct.

Expected failure right now: there is no appmode package, so every
test here fails on import. That failure is correct. It is the
test-first proof. Charles reviews this test with the finished work,
because he chose one prompt for the whole change ("let's continue").

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames.

Qt notes, read before touching this file:
  - QT_QPA_PLATFORM=offscreen is set below, before any Qt import,
    so the window tests run headless in CI and in this sandbox.
  - QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox is set too. The sandbox
    runs as root, and Chromium refuses to run as root without it.
    On a normal user machine it is unnecessary and harmless.
  - One QApplication serves the whole module. Never create a second.
"""
import http.client
import os
import socket
import sys
import tempfile
import time
import types
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import api as api_module
from appmode import shell as shell_module


def _free_port():
    """A loopback port nothing holds right now."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _app():
    """The one QApplication for this module, offscreen, reused."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _pump_until(deadline, cond):
    """Pump the Qt event loop until cond holds or the deadline hits."""
    app = _app()
    while time.time() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    app.processEvents()
    return cond()


class _Seams:
    """Recording stand-ins for the human-only seams."""

    def __init__(self):
        self.paused = []
        self.verified = []
        self.stopped = 0

    def pause(self, trace_id):
        self.paused.append(trace_id)
        return True

    def verify(self, trace_id, read_back):
        self.verified.append((trace_id, read_back))
        return True

    def red_button(self):
        self.stopped += 1


class _StubController:
    """A controller that starts nothing, for window-only tests."""

    def __init__(self):
        self.stopped = 0
        self.proxy_state = None

    def panel_url(self, tier="simple"):
        return f"http://127.0.0.1:8000/?tier={tier}"

    def start(self):
        return True

    def stop(self):
        self.stopped += 1


class PanelUrlTest(unittest.TestCase):
    """The view URL is the loopback panel, tiered."""

    def test_simple_is_the_default_tier(self):
        self.assertEqual(
            shell_module.panel_url(8000),
            "http://127.0.0.1:8000/?tier=simple")

    def test_advanced_tier_in_the_url(self):
        self.assertEqual(
            shell_module.panel_url(8000, "advanced"),
            "http://127.0.0.1:8000/?tier=advanced")


class ControllerTest(unittest.TestCase):
    """The controller starts the proxy and the panel, then stops both."""

    def setUp(self):
        _app()
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        self.old_db_path = api_module.DB_PATH
        self.addCleanup(setattr, api_module, "DB_PATH",
                        self.old_db_path)
        self.addCleanup(self.tmp.cleanup)
        self.controller = None

    def tearDown(self):
        if self.controller is not None:
            self.controller.stop()

    def _start(self, panel_port=None, proxy_port=None,
               instance_name=None):
        self.controller = shell_module.AppController(
            db_path=self.db_path,
            panel_port=panel_port or _free_port(),
            proxy_port=proxy_port or _free_port(),
            instance_name=instance_name or
            f"iris-app-mode-test-{os.getpid()}")
        self.assertTrue(self.controller.start())
        return self.controller

    def test_start_serves_the_panel_and_the_proxy(self):
        """Start serves the panel page and a proxy that blocks."""
        ctl = self._start()
        with urllib.request.urlopen(
                ctl.panel_url("simple"), timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("Runs, live", resp.read().decode())
        conn = http.client.HTTPConnection(
            "127.0.0.1", ctl.proxy_port, timeout=5)
        conn.request("GET", "http://example.com/")
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_stop_frees_both_ports(self):
        """Stop leaves no proxy and no panel listening."""
        ctl = self._start()
        panel_port, proxy_port = ctl.panel_port, ctl.proxy_port
        ctl.stop()
        self.controller = None
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(
                ("127.0.0.1", proxy_port), timeout=2).close()
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(
                ("127.0.0.1", panel_port), timeout=2).close()

    def test_second_start_refuses_and_starts_nothing(self):
        """A second controller refuses; the first one sees it."""
        first = self._start()
        second = shell_module.AppController(
            db_path=self.db_path,
            panel_port=_free_port(), proxy_port=_free_port(),
            instance_name=first.instance_name)
        self.assertFalse(second.start())
        self.assertTrue(second.already_running)
        self.assertIsNone(second.panel_port)
        self.assertTrue(_pump_until(
            time.time() + 5, lambda: first.second_instance_seen))

    def test_port_taken_is_plain_words_and_leaves_nothing(self):
        """A taken panel port fails plainly, with no orphan proxy."""
        panel_port = _free_port()
        proxy_port = _free_port()
        held = socket.socket()
        held.bind(("127.0.0.1", panel_port))
        held.listen(1)
        self.addCleanup(held.close)
        ctl = shell_module.AppController(
            db_path=self.db_path,
            panel_port=panel_port, proxy_port=proxy_port,
            instance_name=f"iris-app-mode-test-{os.getpid()}-taken")
        self.assertFalse(ctl.start())
        self.assertIn(str(panel_port), ctl.start_error)
        self.assertNotIn("Traceback", ctl.start_error)
        self.controller = ctl
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(
                ("127.0.0.1", proxy_port), timeout=2).close()


class WindowTest(unittest.TestCase):
    """The window: native buttons outside the view, tier switch top-right."""

    def setUp(self):
        self.app = _app()
        self.controller = _StubController()
        self.seams = _Seams()
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()
            self.app.processEvents()

    def _window(self, ask_words=None, ask_confirm=None):
        self.window = shell_module.IrisWindow(
            self.controller, seams=self.seams,
            ask_words=ask_words, ask_confirm=ask_confirm)
        return self.window

    def _set_run_url(self, trace_id=None):
        from PySide6.QtCore import QUrl
        url = ("http://127.0.0.1:8000/runs/" + trace_id
               if trace_id else "http://127.0.0.1:8000/")
        self.window.view.setUrl(QUrl(url))
        self.app.processEvents()

    def test_buttons_live_outside_the_view(self):
        """Verify, Pause, and Emergency stop are Qt widgets, not web."""
        win = self._window()
        self.assertEqual(win.verify_button.text(), "Verify")
        self.assertEqual(win.pause_button.text(), "Pause")
        self.assertEqual(win.stop_button.text(), "Emergency stop")
        for button in (win.verify_button, win.pause_button,
                       win.stop_button):
            self.assertIsNot(button.parent(), win.view)
        self.assertIsNotNone(win.view)
        self.assertIn("127.0.0.1:8000", win.view.url().toString())

    def test_tier_switch_changes_the_view_url(self):
        """Flipping the switch reloads the view at the other tier."""
        win = self._window()
        self.assertIn("tier=simple", win.view.url().toString())
        win.tier_switch.setChecked(True)
        self.app.processEvents()
        self.assertIn("tier=advanced", win.view.url().toString())
        win.tier_switch.setChecked(False)
        self.app.processEvents()
        self.assertIn("tier=simple", win.view.url().toString())

    def test_close_stops_the_controller(self):
        """Closing the window stops the proxy and the panel."""
        win = self._window()
        win.close()
        self.app.processEvents()
        self.assertEqual(self.controller.stopped, 1)
        self.window = None

    def test_verify_uses_the_typed_words(self):
        """Verify sends the typed words to the seam, nothing else."""
        win = self._window(ask_words=lambda: "checked the log")
        self._set_run_url("trace-abc")
        win.verify_button.click()
        self.assertEqual(self.seams.verified,
                         [("trace-abc", "checked the log")])

    def test_verify_with_no_words_calls_nothing(self):
        """An empty read-back never reaches the seam."""
        win = self._window(ask_words=lambda: "   ")
        self._set_run_url("trace-abc")
        win.verify_button.click()
        self.assertEqual(self.seams.verified, [])

    def test_pause_uses_the_view_run(self):
        """Pause acts on the Run the view shows."""
        win = self._window()
        self._set_run_url("trace-abc")
        win.pause_button.click()
        self.assertEqual(self.seams.paused, ["trace-abc"])

    def test_pause_with_no_run_calls_nothing(self):
        """On the index page Pause has no Run, so it does nothing."""
        win = self._window()
        self._set_run_url(None)
        win.pause_button.click()
        self.assertEqual(self.seams.paused, [])

    def test_emergency_stop_asks_first(self):
        """Emergency stop runs only after a human confirms."""
        win = self._window(ask_confirm=lambda: True)
        win.stop_button.click()
        self.assertEqual(self.seams.stopped, 1)
        win2 = self._window(ask_confirm=lambda: False)
        win.close()
        self.window = win2
        win2.stop_button.click()
        self.assertEqual(self.seams.stopped, 1)


if __name__ == "__main__":
    unittest.main()
