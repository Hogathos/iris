"""App mode tiers test (test-first, failing until built).

Problem in one sentence: the panel shows everything to everyone, so
a consumer sees diagnostic columns and dev forms they cannot use.

Success criteria for this test Run (human-approved, once per task
type, as a named read-back, before the Run starts):
  task_type = "app mode tiers"
  clause    = "Simple hides Bytes, Seconds, Method, the cutoff form,
               and Redirect; Block, Pause, and Verify stay; no tier
               means show everything, the dev panel unchanged"
  read_back = "named read-back: Simple shows Run, Destination, What,
               Cost, Verdict, Pause only; Advanced shows all; no tier
               shows all"
No approved done = Unverified, never Correct.

Expected failure right now: index and run_detail take no tier, so
every tiered call here fails. That failure is correct. It is the
test-first proof. Charles reviews this test with the finished work,
because he chose one prompt for the whole change ("let's continue").

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames.
"""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import api as api_module
import db as db_module
from devpanel import panel as panel_module


def _body(page):
    """The page text, whether the route returned str or Response."""
    return page if isinstance(page, str) else page.body.decode()


class TierTest(unittest.TestCase):
    """Simple hides the diagnostic columns and the dev forms."""

    def setUp(self):
        """One live Run, one paused Run, on a temp DB."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        conn = db_module.init_db(self.db_path)
        db_module.log_attempt(
            conn, trace_id="trace-live", task_number=1, attempt=1,
            status="success", dest_host="127.0.0.1:18080",
            endpoint="127.0.0.1:18080", method="CONNECT", outcome="",
            created_at="2026-11-22T06:00:00+00:00")
        db_module.log_attempt(
            conn, trace_id="trace-paused", task_number=1, attempt=1,
            status="success", dest_host="127.0.0.1:18080",
            endpoint="127.0.0.1:18080", method="CONNECT", outcome="",
            created_at="2026-11-22T06:00:00+00:00")
        db_module.log_attempt(
            conn, trace_id="trace-paused", task_number=1, attempt=2,
            status="unknown", dest_host="127.0.0.1:18080",
            endpoint="127.0.0.1:18080", method="PAUSE",
            outcome="paused",
            created_at="2026-11-22T06:01:00+00:00")
        conn.commit()
        conn.close()
        self.old_db_path = api_module.DB_PATH
        api_module.DB_PATH = self.db_path
        self.addCleanup(setattr, api_module, "DB_PATH",
                        self.old_db_path)
        self.addCleanup(self.tmp.cleanup)

    def _request(self, state=None):
        """A fake panel request, with or without a live proxy state."""
        return types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(proxy_state=state)))

    def _state(self):
        """A stub proxy state, enough for the cutoff form to render."""
        return types.SimpleNamespace(relay_timeout=30)

    def test_simple_hides_bytes_seconds_method(self):
        """Simple shows no Bytes, Seconds, or Method column."""
        body = _body(panel_module.index(
            self._request(), tier="simple"))
        for header in ("<th>Bytes</th>", "<th>Seconds</th>",
                       "<th>Method</th>"):
            self.assertNotIn(header, body)
        for header in ("<th>Run</th>", "<th>Destination</th>",
                       "<th>What</th>", "<th>Cost</th>",
                       "<th>Verdict</th>", "<th>Pause</th>"):
            self.assertIn(header, body)

    def test_advanced_shows_all_columns(self):
        """Advanced shows every column, including the diagnostic ones."""
        body = _body(panel_module.index(
            self._request(), tier="advanced"))
        for header in ("<th>Run</th>", "<th>Destination</th>",
                       "<th>Method</th>", "<th>What</th>",
                       "<th>Bytes</th>", "<th>Seconds</th>",
                       "<th>Cost</th>", "<th>Verdict</th>",
                       "<th>Pause</th>"):
            self.assertIn(header, body)

    def test_no_tier_shows_everything(self):
        """No tier means show everything: the dev panel is unchanged."""
        body = _body(panel_module.index(self._request()))
        for header in ("<th>Method</th>", "<th>Bytes</th>",
                       "<th>Seconds</th>"):
            self.assertIn(header, body)

    def test_unknown_tier_shows_everything(self):
        """An unknown tier falls back to showing everything, no crash."""
        body = _body(panel_module.index(
            self._request(), tier="bogus"))
        for header in ("<th>Method</th>", "<th>Bytes</th>",
                       "<th>Seconds</th>"):
            self.assertIn(header, body)

    def test_simple_hides_cutoff_form(self):
        """Simple shows no cutoff form; Advanced shows it."""
        simple = _body(panel_module.index(
            self._request(self._state()), tier="simple"))
        self.assertNotIn('action="/settings/relay-timeout"', simple)
        advanced = _body(panel_module.index(
            self._request(self._state()), tier="advanced"))
        self.assertIn('action="/settings/relay-timeout"', advanced)

    def test_simple_hides_redirect_on_the_run_page(self):
        """Simple shows no Redirect form on a live Run; Advanced does."""
        simple = _body(panel_module.run_detail(
            "trace-live", tier="simple"))
        self.assertNotIn("Redirect this Run", simple)
        self.assertNotIn("/redirect", simple)
        advanced = _body(panel_module.run_detail(
            "trace-live", tier="advanced"))
        self.assertIn("Redirect this Run", advanced)

    def test_simple_keeps_block_form(self):
        """Block stays in Simple: it is a safety control."""
        body = _body(panel_module.index(
            self._request(), tier="simple"))
        self.assertIn('action="/settings/block-network"', body)

    def test_simple_keeps_pause_and_verify(self):
        """Pause stays in Simple, and Verify stays on a paused Run."""
        index_body = _body(panel_module.index(
            self._request(), tier="simple"))
        self.assertIn("/runs/trace-live/pause", index_body)
        detail_body = _body(panel_module.run_detail(
            "trace-paused", tier="simple"))
        self.assertIn("/runs/trace-paused/verify", detail_body)

    def test_simple_tier_persists_in_run_links(self):
        """Simple run links and the filter form carry the tier."""
        body = _body(panel_module.index(
            self._request(), tier="simple"))
        self.assertIn("/runs/trace-live?tier=simple", body)
        self.assertIn('name="tier" value="simple"', body)

    def test_run_detail_back_link_keeps_tier(self):
        """The run page links back to the list at the same tier."""
        body = _body(panel_module.run_detail(
            "trace-live", tier="simple"))
        self.assertIn('href="/?tier=simple"', body)

    def test_pause_redirect_keeps_tier(self):
        """Pausing from Simple lands back on the Simple run page."""
        state = types.SimpleNamespace(
            pause_run=lambda trace_id, reason="": True)
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(proxy_state=state)))
        resp = panel_module.pause_button(
            "trace-live", request, tier="simple")
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith(
            "/runs/trace-live?tier=simple"))


if __name__ == "__main__":
    unittest.main()
