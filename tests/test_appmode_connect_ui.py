"""App-mode Connect button plus agent-restart step (test-first).

Problem in one sentence: the window has no Connect button, so a
consumer cannot connect an agent to the Iris proxy and restart it
from the app.

Success criteria for this test Run (human-approved, once per task
type, as a named read-back, before the Run starts):
  task_type = "app mode connect ui"
  clause    = "a Connect button in the top bar picks an agent,
               connects it, shows the plain-words result, warns
               before restart with a Save button, and restarts
               Cursor on Windows"
  read_back = "named read-back: button exists and is wired;
               connect success shows the result; refusal shows
               the refusal and restarts nothing; the Save button
               restarts nothing; restart runs only after the
               warning plus confirmation"
No approved done = Unverified, never Correct.

Expected failure right now: the window has no Connect button and
there is no restart module, so every test here fails. That failure
is correct. It is the test-first proof. Charles reviews this test
before it is implemented.

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

import os
import sys
import unittest

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
for _proxy_key in ("NO_PROXY", "no_proxy"):
    _seen = [p.strip() for p in os.environ.get(_proxy_key, "").split(",") if p.strip()]
    for _host in ("127.0.0.1", "localhost"):
        if _host not in _seen:
            _seen.append(_host)
    os.environ[_proxy_key] = ",".join(_seen)

from appmode import shell as shell_module
from appmode.connect import Refused


def _app():
    """The one QApplication for this module, offscreen, reused."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _delete_window(window):
    """Destroy a window now, on the rule that works for this OS."""
    window.close()
    if sys.platform == "win32":
        from shiboken6 import delete as _delete_qobject

        _delete_qobject(window)
    else:
        from PySide6.QtWidgets import QApplication

        window.deleteLater()
        QApplication.sendPostedEvents()


def tearDownModule():
    """Destroy all Qt state so none leaks into the next module."""
    import gc
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        for widget in app.topLevelWidgets():
            widget.close()
        if sys.platform == "win32":
            from shiboken6 import delete as _delete_qobject

            _delete_qobject(app)
        else:
            app.deleteLater()
            QApplication.sendPostedEvents()
        del app
    gc.collect()


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

    def __init__(self, proxy_port=8888):
        self.stopped = 0
        self.proxy_state = None
        self.proxy_port = proxy_port

    def panel_url(self, tier="simple"):
        return f"http://127.0.0.1:8000/?tier={tier}"

    def start(self):
        return True

    def stop(self):
        self.stopped += 1


class _ConnectStubs:
    """Recording stand-ins for the connect flow."""

    def __init__(self, agent="cursor", connect_result=None, restart_result=None, restart_choice="restart"):
        self.agent = agent
        self.connect_calls = []
        self.restart_calls = []
        self.messages = []
        self.restart_prompts = []
        self.connect_result = (
            connect_result if connect_result is not None else "Cursor now routes through http://127.0.0.1:8888."
        )
        self.restart_result = restart_result if restart_result is not None else "Cursor is restarting."
        # The restart warning choice: "restart", "save", or "cancel".
        self.restart_choice = restart_choice
        self.connect_error = None

    def pick_agent(self):
        return self.agent

    def show_message(self, text):
        self.messages.append(text)

    def ask_restart(self, agent_name):
        self.restart_prompts.append(agent_name)
        return self.restart_choice

    def connect_fn(self, agent_id, proxy_url, home):
        self.connect_calls.append((agent_id, proxy_url, home))
        if self.connect_error is not None:
            raise self.connect_error
        return self.connect_result

    def restart_fn(self, agent_id):
        self.restart_calls.append(agent_id)
        return self.restart_result


class WindowConnectTest(unittest.TestCase):
    """The Connect button: pick an agent, connect, show the result."""

    def setUp(self):
        self.app = _app()
        self.controller = _StubController(proxy_port=8888)
        self.seams = _Seams()
        self.stubs = _ConnectStubs()
        self.window = None

    def tearDown(self):
        if self.window is not None:
            _delete_window(self.window)
            self.window = None

    def _window(self, stubs=None):
        stubs = stubs if stubs is not None else self.stubs
        self.window = shell_module.IrisWindow(
            self.controller,
            seams=self.seams,
            pick_agent=stubs.pick_agent,
            show_message=stubs.show_message,
            ask_restart=stubs.ask_restart,
            connect_fn=stubs.connect_fn,
            restart_fn=stubs.restart_fn,
        )
        return self.window

    def test_connect_button_exists_in_the_top_bar(self):
        """Connect sits beside Verify, Pause, and Emergency stop."""
        win = self._window()
        self.assertEqual(win.connect_button.text(), "Connect")
        for button in (win.verify_button, win.pause_button, win.stop_button, win.connect_button):
            self.assertIsNot(button.parent(), win.view)

    def test_connect_success_shows_the_result(self):
        """Picking an agent connects it and shows the plain words."""
        win = self._window()
        win.connect_button.click()
        self.assertEqual(len(self.stubs.connect_calls), 1)
        agent_id, proxy_url, home = self.stubs.connect_calls[0]
        self.assertEqual(agent_id, "cursor")
        self.assertEqual(proxy_url, "http://127.0.0.1:8888")
        self.assertTrue(home)
        self.assertIn(self.stubs.connect_result, self.stubs.messages)

    def test_connect_refusal_shows_the_refusal_and_restarts_nothing(self):
        """A refusal shows in plain words and changes nothing."""
        self.stubs.connect_error = Refused("Cursor already routes through http://127.0.0.1:9999. Iris changed nothing.")
        win = self._window()
        win.connect_button.click()
        self.assertEqual(len(self.stubs.connect_calls), 1)
        self.assertEqual(len(self.stubs.messages), 1)
        self.assertIn("already routes", self.stubs.messages[0])
        self.assertEqual(self.stubs.restart_calls, [])
        self.assertEqual(self.stubs.restart_prompts, [])

    def test_picker_cancel_connects_nothing(self):
        """Closing the picker without picking does nothing."""
        self.stubs.agent = None
        win = self._window()
        win.connect_button.click()
        self.assertEqual(self.stubs.connect_calls, [])
        self.assertEqual(self.stubs.messages, [])
        self.assertEqual(self.stubs.restart_calls, [])

    def test_restart_runs_after_confirmation(self):
        """After connect, a confirmed restart runs and shows."""
        win = self._window()
        win.connect_button.click()
        self.assertEqual(self.stubs.restart_prompts, ["Cursor"])
        self.assertEqual(self.stubs.restart_calls, ["cursor"])
        self.assertIn(self.stubs.restart_result, self.stubs.messages)

    def test_restart_skipped_without_confirmation(self):
        """Without confirmation, the agent is not restarted."""
        self.stubs.restart_choice = "cancel"
        win = self._window()
        win.connect_button.click()
        self.assertEqual(self.stubs.restart_prompts, ["Cursor"])
        self.assertEqual(self.stubs.restart_calls, [])
        self.assertEqual(len(self.stubs.messages), 1)

    def test_save_option_restarts_nothing(self):
        """The "I'll save first" button means save first: no restart.

        The button does not save the agent's work itself. Iris
        cannot save from outside, and Iris cannot tell the agent
        to save. The proxy sees traffic. It does not control the
        agent. The button closes the dialog so the user can save
        in the agent, then restart. The window tells the user to
        save first, in plain words.
        """
        self.stubs.restart_choice = "save"
        win = self._window()
        win.connect_button.click()
        self.assertEqual(self.stubs.restart_prompts, ["Cursor"])
        self.assertEqual(self.stubs.restart_calls, [])
        self.assertTrue(any("save" in message.lower() for message in self.stubs.messages))


class RestartTest(unittest.TestCase):
    """restart_agent: real restart on Windows Cursor, words elsewhere."""

    def test_claude_code_restart_is_a_manual_instruction(self):
        """Iris cannot restart a CLI, so it says how to do it."""
        from appmode import restart as restart_module

        calls = []

        def _no_process(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("no process call for a CLI agent")

        result = restart_module.restart_agent("claude-code", find_exe=_no_process, restart_process=_no_process)
        self.assertIn("claude", result.lower())
        self.assertEqual(calls, [])

    def test_continue_restart_is_a_manual_instruction(self):
        """Iris cannot restart a VS Code extension, so it says how."""
        from appmode import restart as restart_module

        calls = []

        def _no_process(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("no process call for an extension")

        result = restart_module.restart_agent("continue", find_exe=_no_process, restart_process=_no_process)
        self.assertIn("vs code", result.lower())
        self.assertEqual(calls, [])

    def test_cursor_restart_on_windows_relaunches(self):
        """On Windows, Cursor is closed and reopened by Iris."""
        from appmode import restart as restart_module

        calls = []

        def _find_exe(agent_id):
            return "C:\\Users\\test\\AppData\\Local\\Programs"
            "\\cursor\\Cursor.exe"

        def _restart_process(exe_path, process_name):
            calls.append((exe_path, process_name))
            return True

        result = restart_module.restart_agent("cursor", platform="win32", find_exe=_find_exe, restart_process=_restart_process)
        self.assertEqual(len(calls), 1)
        self.assertIn("Cursor.exe", calls[0][1])
        self.assertIn("restart", result.lower())

    def test_cursor_restart_without_exe_is_a_manual_instruction(self):
        """No Cursor exe found means words, not a failed kill."""
        from appmode import restart as restart_module

        calls = []

        def _restart_process(exe_path, process_name):
            calls.append((exe_path, process_name))
            return True

        result = restart_module.restart_agent(
            "cursor", platform="win32", find_exe=lambda a: None, restart_process=_restart_process
        )
        self.assertIn("cursor", result.lower())
        self.assertEqual(calls, [])

    def test_cursor_restart_off_windows_is_a_manual_instruction(self):
        """Off Windows, Cursor gets words, not a process call."""
        from appmode import restart as restart_module

        calls = []

        def _restart_process(exe_path, process_name):
            calls.append((exe_path, process_name))
            return True

        result = restart_module.restart_agent(
            "cursor", platform="darwin", find_exe=lambda a: "/Applications/Cursor.app", restart_process=_restart_process
        )
        self.assertIn("cursor", result.lower())
        self.assertEqual(calls, [])

    def test_unknown_agent_refuses(self):
        """An unknown agent refuses in plain words."""
        from appmode import restart as restart_module

        with self.assertRaises(Refused):
            restart_module.restart_agent("unknown-agent")

    @given(
        agent_id=st.sampled_from(["claude-code", "cursor", "continue"]),
        platform=st.sampled_from(["win32", "darwin", "linux"]),
        exe_found=st.booleans(),
    )
    def test_restart_agent_property(self, agent_id, platform, exe_found):
        """Property: restart_agent restarts only Cursor on Windows.

        For every known agent and platform, restart_agent either
        performs a real restart or returns a manual instruction.
        It never calls the process function for a non-Cursor agent,
        a non-Windows platform, or a missing exe. This is the
        oracle for this feature: one independent check of the
        restart decision, beyond the unit tests.
        """
        from appmode import restart as restart_module

        calls = []

        def _find_exe(agent):
            return "C:\\fake\\Cursor.exe" if exe_found else None

        def _restart_process(exe_path, process_name):
            calls.append((exe_path, process_name))
            return True

        result = restart_module.restart_agent(
            agent_id, platform=platform, find_exe=_find_exe, restart_process=_restart_process
        )
        if agent_id == "cursor" and platform == "win32" and exe_found:
            self.assertEqual(len(calls), 1)
            self.assertIn("restart", result.lower())
        else:
            self.assertEqual(calls, [])
            self.assertTrue(result)
