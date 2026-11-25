"""Prescriptive spec tests, test-first: the consumer look and the dev look.

Section 12 of specs/app-mode/prescriptive-spec.md lists 14 tests.
Charles reviews this file before the implementation. Each test pins
exact words, layout, or behavior from the spec. Every one of them
fails until the spec is built. New behavior only.

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames. No listening sockets:
ProxyState is built directly on a temp DB, panel routes are called
as plain functions, Qt runs offscreen. Tests that need a live
server belong to the bench files, not here.

Qt notes, read before touching this file:
  - QT_QPA_PLATFORM=offscreen is set below, before any Qt import.
  - One QApplication serves the whole module. Never create a second.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
for _proxy_key in ("NO_PROXY", "no_proxy"):
    _seen = [p.strip() for p in os.environ.get(_proxy_key, "").split(",")
             if p.strip()]
    for _host in ("127.0.0.1", "localhost"):
        if _host not in _seen:
            _seen.append(_host)
    os.environ[_proxy_key] = ",".join(_seen)

import api as api_module
import db as db_module
import proxy as proxy_module
from appmode import shell as shell_module
from devpanel import panel as panel_module
from devpanel import states as states_module


def _app():
    """The one QApplication for this module, offscreen, reused."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _fresh_state(tmp):
    """A live ProxyState on a temp journal. No sockets, no servers."""
    db_path = os.path.join(tmp.name, "attempts.db")
    return proxy_module.ProxyState(db_path), db_path


class _FakeController:
    """The smallest controller the window needs. No sockets."""

    def __init__(self, db_path, panel_port=8191, proxy_port=8891):
        self.db_path = db_path
        self.panel_port = panel_port
        self.proxy_port = proxy_port
        self.proxy_state = None
        self.stopped = 0

    def panel_url(self, tier="simple"):
        return shell_module.panel_url(self.panel_port, tier)

    def stop(self):
        self.stopped += 1


class _FakeSeams:
    """Human-only seams that record instead of acting."""

    def __init__(self):
        self.calls = []

    def pause(self, trace_id):
        self.calls.append(("pause", trace_id))
        return True

    def verify(self, trace_id, read_back):
        self.calls.append(("verify", trace_id, read_back))
        return True

    def red_button(self):
        self.calls.append(("red",))
        return True

    def set_watching(self, on):
        self.calls.append(("watching", on))
        return True

    def redirect(self, trace_id, dest):
        self.calls.append(("redirect", trace_id, dest))
        return True

    def allow_place(self, dest):
        self.calls.append(("allow", dest))
        return True

    def run_demo(self):
        self.calls.append(("demo",))
        return "demo-trace-id"


def _window(db_path, seams=None, **kwargs):
    """A window on a fake controller. Offscreen, never shown."""
    _app()
    controller = _FakeController(db_path)
    window = shell_module.IrisWindow(
        controller, seams=seams or _FakeSeams(), **kwargs)
    window._stack.setCurrentWidget(window._web_page)
    return window


def _panel_request(state=None, headers=None):
    """A fake panel request carrying state and headers, no server."""
    app_state = types.SimpleNamespace(
        proxy_state=state, panel_token=None, panel_port=None)
    app = types.SimpleNamespace(state=app_state)
    return types.SimpleNamespace(app=app, headers=headers or {})


class FirstRunTest(unittest.TestCase):
    """Spec 12.1: the first-run screen and its two paths."""

    def test_first_run_words_and_paths(self):
        from appmode import demo as demo_module  # noqa: F401
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        window = _window(os.path.join(tmp.name, "attempts.db"))
        self.addCleanup(window.close)
        page = window._first_run_page
        words = " ".join(
            label.text() for label in page.findChildren(
                __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
        for sentence in (
                "Iris watches what your agent does on this computer.",
                "Everything stays on this computer. Iris needs no account.",
                "It sees where each tool call goes, how big it is, "
                "and how long it takes.",
                "When your agent says it did something, Iris looks "
                "for itself and tells you: Correct, Incorrect, "
                "or Unverified.",
                "The demo runs on this computer only. It costs $0.00."):
            self.assertIn(sentence, words)
        self.assertEqual(window._demo_button.text(), "Run the demo")
        self.assertEqual(window._skip_link.text(),
                         "Skip the demo and open the dashboard")
        seams = _FakeSeams()
        window2 = _window(os.path.join(tmp.name, "j2.db"), seams=seams)
        self.addCleanup(window2.close)
        window2._choose_demo()
        self.assertEqual(seams.calls[-1], ("demo",))
        self.assertIs(window2._stack.currentWidget(), window2._web_page)
        window3 = _window(os.path.join(tmp.name, "j3.db"))
        self.addCleanup(window3.close)
        window3._choose_skip()
        self.assertIs(window3._stack.currentWidget(), window3._web_page)


class DemoTest(unittest.TestCase):
    """Spec 12.2: a real demo, real notes, a real read-back."""

    def test_demo_three_claims_and_a_stop(self):
        from appmode import demo as demo_module
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        notebook = os.path.join(tmp.name, "demo-notebook.json")
        trace_id = demo_module.run_demo(
            state, notebook, interval_seconds=0)
        with open(notebook, encoding="utf-8") as handle:
            notes = json.load(handle)
        self.assertEqual(len(notes), 2)
        conn = sqlite3.connect(db_path)
        try:
            claims = db_module.load_claims(conn, trace_id)
        finally:
            conn.close()
        self.assertEqual([c["verdict"] for c in claims],
                         ["Correct", "Unverified", "Incorrect"])
        self.assertEqual(
            [c["claim"] for c in claims],
            ['I saved a note called "groceries".',
             "I checked the weather service.",
             "I saved 3 notes in total."])
        self.assertEqual(
            [c["found"] for c in claims],
            ['Iris looked in the demo notebook. The note "groceries" '
             "is there.",
             "That service is on another computer. Iris only looks at "
             "this computer, so it could not check.",
             "Iris looked in the demo notebook. It holds 2 notes, "
             "not 3."])
        rows = db_module.get_connection(db_path)
        self.assertEqual(states_module.run_state(
            _rows_for(db_path, trace_id)), "STOPPED")
        rows.close()

    def test_demo_detail_says_what_iris_found(self):
        from appmode import demo as demo_module
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        notebook = os.path.join(tmp.name, "demo-notebook.json")
        trace_id = demo_module.run_demo(
            state, notebook, interval_seconds=0)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        html = panel_module.run_detail(
            trace_id, tier="simple",
            request=_panel_request(state))
        self.assertIn("Iris stopped this Run. A claim was Incorrect.",
                      html)


def _rows_for(db_path, trace_id):
    """Journal rows for one trace, oldest first, plain dicts."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT * FROM attempts WHERE trace_id = ? ORDER BY id",
            (trace_id,))]
    finally:
        conn.close()


class VerdictWordsTest(unittest.TestCase):
    """Spec 12.3: Correct, Incorrect, Unverified on screen. Never Unknown."""

    def test_no_unknown_on_screen(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        request = _panel_request(state)
        index_html = panel_module.index(request, tier="simple")
        for word in ("Correct", "Incorrect", "Unverified"):
            self.assertIn(word, index_html)
        self.assertNotIn("Unknown", index_html)
        self.assertNotIn("unknown", index_html)
        detail_html = panel_module.run_detail(
            "no-such-run", tier="simple", request=request)
        self.assertNotIn("Unknown", detail_html)
        window = _window(os.path.join(tmp.name, "j.db"))
        self.addCleanup(window.close)
        help_words = window._verdict_help_text()
        self.assertNotIn("Unknown", help_words)
        for word in ("Correct", "Incorrect", "Unverified"):
            self.assertIn(word, help_words)


class RunLabelTest(unittest.TestCase):
    """Spec 12.4: Run plus the first 8 of the trace id. No numbers."""

    def test_run_label_is_trace_prefix(self):
        trace_id = "abcdef1234567890"
        self.assertEqual(panel_module._short(trace_id), "Run abcdef12")
        self.assertEqual(shell_module.short_run_name(trace_id),
                         panel_module._short(trace_id))
        self.assertEqual(panel_module.SPOT,
                         shell_module.DEFAULT_PROXY_PORT)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        db_module.log_attempt(
            state.conn, trace_id=trace_id, task_number=1, attempt=1,
            status="success", outcome="allowed",
            dest_host="127.0.0.1", method="GET")
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        html = panel_module.index(_panel_request(state), tier="simple")
        self.assertIn("Run abcdef12", html)
        self.assertIsNone(re.search(r"Run #?\d", html))


class VerifyGateTest(unittest.TestCase):
    """Spec 12.5: Verify asks in plain words, stamps, and lets go again."""

    def test_verify_dialog_stamps_and_paused_run_goes_again(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        trace_id = "verify" + "0" * 10
        db_module.log_attempt(
            state.conn, trace_id=trace_id, task_number=1, attempt=1,
            status="success", outcome="allowed",
            dest_host="127.0.0.1", method="GET")
        self.assertTrue(state.pause_run(trace_id, reason="human pause"))
        seams = _FakeSeams()
        window = _window(os.path.join(tmp.name, "j.db"), seams=seams,
                         ask_words=lambda: "I checked the notebook.")
        self.addCleanup(window.close)
        dialog = window._verify_dialog()
        self.assertEqual(dialog.windowTitle(), "Verify")
        buttons = {button.text() for button in dialog.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QPushButton"])
            .QPushButton)}
        self.assertEqual(buttons, {"Cancel", "Verify"})
        self.assertIn("What was checked, in plain words:",
                      dialog.findChild(
                          __import__("PySide6.QtWidgets",
                                     fromlist=["QLabel"]).QLabel).text())
        words = "I checked the notebook against the claim."
        self.assertTrue(panel_module.devpanel_verify(
            state, trace_id, words))
        allowed, _why = state.retry_run(trace_id)
        self.assertTrue(allowed)
        window2 = _window(os.path.join(tmp.name, "j2.db"),
                          seams=_FakeSeams(),
                          ask_words=lambda: "   ")
        self.addCleanup(window2.close)
        window2._seams.calls.clear()
        window2._on_verify_for("sometrace")
        self.assertEqual(window2._seams.calls, [])


class EmergencyStopTest(unittest.TestCase):
    """Spec 12.6: one click stops all. Confirm only to turn it off."""

    def test_one_click_stops_everything(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        for trace_id in ("stop-a-12345678", "stop-b-12345678"):
            db_module.log_attempt(
                state.conn, trace_id=trace_id, task_number=1, attempt=1,
                status="success", outcome="allowed",
                dest_host="127.0.0.1", method="GET")
        state.red_button(reason="human red button")
        for trace_id in ("stop-a-12345678", "stop-b-12345678"):
            self.assertEqual(
                states_module.run_state(
                    _rows_for(db_path, trace_id)), "STOPPED")
        seams = _FakeSeams()
        asked = []
        window = _window(os.path.join(tmp.name, "j.db"), seams=seams,
                         ask_confirm=lambda: asked.append(True))
        self.addCleanup(window.close)
        window._on_stop()
        self.assertEqual(asked, [])
        self.assertEqual(seams.calls, [("red",)])
        self.assertIn("red", window.stop_button.styleSheet().lower())
        dialog = window._emergency_off_dialog()
        self.assertEqual(dialog.windowTitle(), "Let agents work again?")
        self.assertIn("Runs that were stopped stay stopped.",
                      dialog.text())
        _kind, words, _buttons = window._strip_for(emergency=True)
        self.assertIn("Emergency stop is on.", words)
        self.assertIn("Close it yourself if you want it fully off.",
                      words)

    def test_stop_survives_restart(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        state.red_button(reason="human red button")
        state.conn.close()
        conn = sqlite3.connect(db_path)
        try:
            saved = db_module.load_red_button(conn)
        finally:
            conn.close()
        self.assertIsNotNone(saved)


class LoopbackSecurityTest(unittest.TestCase):
    """Spec 12.7: per-launch token on every post, Host check on all."""

    def test_token_is_fresh_and_posts_need_it(self):
        first = panel_module.new_panel_token()
        second = panel_module.new_panel_token()
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        token = panel_module.install_identity(port=8199)
        try:
            self.assertTrue(panel_module.check_host("127.0.0.1:8199"))
            self.assertTrue(panel_module.check_host("localhost:8199"))
            self.assertFalse(panel_module.check_host("evil.example:8199"))
            self.assertFalse(panel_module.check_host("127.0.0.1:9999"))
            self.assertTrue(panel_module.check_token(token))
            self.assertFalse(panel_module.check_token("stale-token"))
            refused = panel_module.pause_button(
                "trace-x", _panel_request(
                    state, headers={"host": "127.0.0.1:8199"}),
                tier="", token="wrong-token")
            self.assertEqual(refused.status_code, 403)
            self.assertIn("This page is out of date. Reload it.",
                          refused.body.decode())
            rows_before = db_module.fetch_totals(state.conn)["attempts"]
            self.assertEqual(
                db_module.fetch_totals(state.conn)["attempts"],
                rows_before)
        finally:
            panel_module.clear_identity()

    def test_token_lives_in_memory_only(self):
        import inspect
        maker_source = inspect.getsource(panel_module.new_panel_token)
        self.assertIn("secrets", maker_source)
        panel_source = inspect.getsource(panel_module)
        self.assertNotRegex(panel_source, r"open\([^)]+token")
        self.assertNotIn("print", maker_source)


class SpendingLimitTest(unittest.TestCase):
    """Spec 12.8: a set limit blocks calls Iris cannot price."""

    def test_limit_blocks_unpriceable_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, _db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        place = "prices.unknown:443"
        self.assertIsNone(state.unpriceable_blocked(place))
        state.set_daily_limit(10.0)
        reason = state.unpriceable_blocked(place)
        self.assertIn("daily limit", reason)
        self.assertIsNone(
            state.unpriceable_blocked("127.0.0.1:8891"))
        state.allow_place(place)
        self.assertIsNone(state.unpriceable_blocked(place))
        state.unallow_place(place)
        state.set_daily_limit(None)
        self.assertIsNone(state.unpriceable_blocked(place))

    def test_blocked_call_costs_nothing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        state.set_daily_limit(10.0)
        trace_id = "spend" + "0" * 11
        reason = state.unpriceable_blocked("prices.unknown:443")
        state.note_blocked_call(trace_id, "prices.unknown:443", reason)
        rows = _rows_for(db_path, trace_id)
        self.assertEqual(rows[-1]["outcome"], "blocked")
        self.assertEqual(api_module.attempt_cost(rows[-1]), 0.0)


class AllowOneClickTest(unittest.TestCase):
    """Spec 12.9: allowing a place is one click, with Undo after."""

    def test_allow_is_one_click_with_undo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        state.set_daily_limit(10.0)
        place = "prices.unknown:443"
        trace_id = "allow" + "0" * 11
        state.note_blocked_call(
            trace_id, place, state.unpriceable_blocked(place))
        window = _window(os.path.join(tmp.name, "j.db"))
        self.addCleanup(window.close)
        kind, words, buttons = window._strip_for(
            blocked_place=place)
        self.assertIn(f"Iris blocked a call to {place}.", words)
        self.assertIn(f"Allow {place}", buttons)
        window._allow_place(place, state)
        self.assertTrue(state.is_allowed_place(place))
        kind2, words2, buttons2 = window._strip_for(
            blocked_place=place)
        self.assertIn(f"{place} is allowed.", words2)
        self.assertIn("Undo", buttons2)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        limits_html = panel_module.limits_page(_panel_request(state))
        self.assertIn(place, limits_html)
        self.assertIn("Allow", limits_html)


class JournalWordsTest(unittest.TestCase):
    """Spec 12.10: journal everywhere. Saved file is iris-journal-{date}."""

    def test_screens_say_journal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        request = _panel_request(state)
        rendered = (
            panel_module.index(request, tier="simple")
            + panel_module.limits_page(request)
            + panel_module.tools_page(request))
        self.assertIn("journal", rendered)
        self.assertNotRegex(rendered, r"[Rr]ecord")
        window = _window(os.path.join(tmp.name, "j.db"))
        self.addCleanup(window.close)
        names = [action.text()
                 for menu in window.menuBar().findChildren(
                     __import__("PySide6.QtWidgets",
                                fromlist=["QMenu"]).QMenu)
                 for action in menu.actions()]
        self.assertIn("Save a copy of the journal\u2026", names)
        self.assertEqual(window.default_journal_name("2026-11-25"),
                         "iris-journal-2026-11-25")
        self.assertIn("It holds no message text and no keys.",
                      window._save_note_text())


class DevpanelLookTest(unittest.TestCase):
    """Spec 12.11: same strings as the app. Metadata only, never raw."""

    def test_same_strings_and_metadata_only(self):
        self.assertIs(shell_module.VERIFY_TOOLTIP,
                      panel_module.VERIFY_TOOLTIP)
        self.assertIs(shell_module.PAUSE_TOOLTIP,
                      panel_module.PAUSE_TOOLTIP)
        self.assertIs(shell_module.EMERGENCY_TOOLTIP,
                      panel_module.EMERGENCY_TOOLTIP)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        trace_id = "calls" + "0" * 11
        db_module.log_attempt(
            state.conn, trace_id=trace_id, task_number=1, attempt=1,
            status="success", outcome="allowed", dest_host="127.0.0.1",
            endpoint="/notes", method="POST", bytes_in=18,
            bytes_out=200, latency_seconds=0.012)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        html = panel_module.run_detail(
            trace_id, tier="advanced",
            request=_panel_request(state)).lower()
        for header in ("#", "time", "kind", "place", "path", "sent",
                       "received", "took", "outcome"):
            self.assertIn(header, html)
        for banned in ("payload", "replay", "resend a call", "raw",
                        "trace_id", "dest_host", "attempt_cost"):
            self.assertNotIn(banned, html)


class ConsumerTiersTest(unittest.TestCase):
    """Spec 12.12: the consumer build has no Developer pages or routes."""

    def test_developer_routes_are_gone_in_consumer_build(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        request = _panel_request(state)
        self.assertIn("Runs", panel_module.index(request, tier="simple"))
        panel_module.set_consumer_build(True)
        try:
            gone = panel_module.swarm_map(request, tier="")
            self.assertEqual(gone.status_code, 404)
            developer = panel_module.index(request, tier="developer")
            self.assertEqual(developer.status_code, 404)
            settings = panel_module.relay_timeout_button(
                request, seconds="30")
            self.assertEqual(settings.status_code, 404)
        finally:
            panel_module.set_consumer_build(False)


class TeardownTest(unittest.TestCase):
    """Spec 12.13: close stops the controller and frees the view by hand."""

    def test_close_stops_controller_and_demo_and_frees_view(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        controller = _FakeController(os.path.join(tmp.name, "j.db"))
        _app()
        window = shell_module.IrisWindow(controller, seams=_FakeSeams())
        window._demo_worker = _RecordingWorker()
        window.close()
        self.assertEqual(controller.stopped, 1)
        self.assertTrue(window._demo_worker.joined)
        from shiboken6 import isValid
        self.assertFalse(isValid(window.view_host))
        self.assertFalse(isValid(window.view))


class _RecordingWorker:
    """A demo worker stand-in that records join instead of running."""

    def __init__(self):
        self.joined = False
        self.stopped = False

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


class MoreDetailTabsTest(unittest.TestCase):
    """Spec 12.14: More detail off shows claims only; on adds the tabs."""

    def test_detail_tabs_follow_more_detail(self):
        from appmode import demo as demo_module
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state, db_path = _fresh_state(tmp)
        self.addCleanup(state.conn.close)
        notebook = os.path.join(tmp.name, "demo-notebook.json")
        trace_id = demo_module.run_demo(
            state, notebook, interval_seconds=0)
        old = api_module.DB_PATH
        api_module.DB_PATH = db_path
        self.addCleanup(setattr, api_module, "DB_PATH", old)
        request = _panel_request(state)
        simple = panel_module.run_detail(trace_id, tier="simple",
                                         request=request)
        self.assertIn("What the agent said", simple)
        self.assertNotIn("How Iris decided", simple)
        advanced = panel_module.run_detail(trace_id, tier="advanced",
                                           request=request)
        for tab in ("Claims", "Calls", "Why", "Cost"):
            self.assertIn(tab, advanced)
        self.assertIn("How Iris decided", advanced)
        self.assertIn("Where the calls went", advanced)
        window = _window(os.path.join(tmp.name, "j.db"))
        self.addCleanup(window.close)
        plain_names = window._iris_menu_names(advanced=False)
        self.assertNotIn("Developer page", plain_names)
        detail_names = window._iris_menu_names(advanced=True)
        self.assertIn("Developer page", detail_names)
