"""Devtool panel polish test (test-first, failing until built).

Problem in one sentence: the panel hides words a human already typed,
refuses a cap or a block without saying what form the destination
takes, and blocked-only misses a Run past the latest 100 traces.

Success criteria for this test Run (human-approved, once per task
type, as a named read-back, before the Run starts):
  task_type = "dev panel text and filter"
  clause    = "words shown match the journal; refusals name the form;
               blocked-only finds an old blocked Run"
  read_back = "named read-back: page words match the journal row;
               refusal page names the form; blocked-only lists the
               old blocked Run"
No approved done = Unverified, never Correct.

Expected failure right now: _row_words says "unverified: nothing
checked this yet" for a VERIFY gate row; the cap and block refusal
pages do not name the destination form; blocked-only finds nothing
when the blocked Run is older than the latest 100 traces. Those
failures are correct. They are the test-first proof. Charles reviews
this test with the finished work, because he chose one prompt for
the whole change ("go fix all of it").

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames.
"""
import os
import sqlite3
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import api as api_module
import db as db_module
import proxy as proxy_module
from devpanel import panel as panel_module


def _body(page):
    """The page text, whether the route returned str or Response."""
    return page if isinstance(page, str) else page.body.decode()


class PanelPolishWordsTest(unittest.TestCase):
    """The What column shows the words the human typed."""

    def test_verify_gate_row_shows_the_words(self):
        """A VERIFY gate row shows its journal reason, not Unverified."""
        row = {"method": "VERIFY", "outcome": "", "status": "unknown",
               "reason": "verify ok: test",
               "dest_host": "127.0.0.1:18080", "endpoint": "/echo"}
        self.assertEqual(panel_module._row_words(row), "verify ok: test")

    def test_verify_verdict_row_keeps_its_words(self):
        """A read-back verdict row keeps its own words."""
        row = {"method": "VERIFY", "outcome": "verified",
               "status": "success",
               "reason": "verified Correct: read back matched the claim."}
        self.assertEqual(
            panel_module._row_words(row),
            "verified Correct: read back matched the claim.")


class PanelPolishRefusalTest(unittest.TestCase):
    """A refused cap or block names the destination form."""

    def setUp(self):
        """Fresh proxy state on a temp DB; no traffic runs."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        self.server = proxy_module.serve(
            "127.0.0.1", 0, self.db_path)
        self.old_db_path = api_module.DB_PATH
        api_module.DB_PATH = self.db_path
        # addCleanup runs LIFO: the temp dir must go last, after
        # the server stops and the DB connection closes. Windows
        # refuses to delete a folder holding an open file; Linux
        # lets it go, which is why this only failed on Windows.
        self.addCleanup(setattr, api_module, "DB_PATH",
                        self.old_db_path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.server.state.conn.close)
        self.addCleanup(self.server.server_close)

    def _request(self, state):
        """A fake panel request that carries the live proxy state."""
        return types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(proxy_state=state)))

    def test_cap_refusal_names_the_destination_form(self):
        """A URL as the cap destination is refused with the form named."""
        resp = panel_module.cap_button(
            self._request(self.server.state),
            dest="https://example.com", dollars="5.00")
        self.assertEqual(resp.status_code, 400)
        body = resp.body.decode()
        self.assertIn("127.0.0.1:18080", body)
        self.assertIn("not a web address", body)

    def test_block_refusal_names_the_destination_form(self):
        """A URL as the block destination is refused with the form named."""
        resp = panel_module.block_network_button(
            self._request(self.server.state), dest="https://example.com")
        self.assertEqual(resp.status_code, 400)
        body = resp.body.decode()
        self.assertIn("127.0.0.1:18080", body)
        self.assertIn("not a web address", body)

    def test_forms_hint_the_destination_form(self):
        """The Spend cap and Block forms hint the label form."""
        page = panel_module.index(self._request(self.server.state))
        body = _body(page)
        self.assertIn("a destination label, like 127.0.0.1:18080", body)


class PanelPolishFilterTest(unittest.TestCase):
    """A filter searches past the latest 100 traces."""

    def setUp(self):
        """105 traces; the blocked one is the oldest of all."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        conn = db_module.init_db(self.db_path)
        for i in range(105):
            trace_id = f"trace-{i:03d}"
            if i == 0:
                outcome, status = "blocked", "failure"
                dest = "example.com"
            else:
                outcome, status = "", "success"
                dest = "127.0.0.1:18080"
            db_module.log_attempt(
                conn, trace_id=trace_id, task_number=1, attempt=1,
                status=status, dest_host=dest, endpoint=dest,
                method="CONNECT", outcome=outcome,
                created_at=f"2026-11-22T06:{i // 60:02d}:{i % 60:02d}"
                            ":00+00:00")
        conn.commit()
        conn.close()
        self.old_db_path = api_module.DB_PATH
        api_module.DB_PATH = self.db_path
        self.addCleanup(setattr, api_module, "DB_PATH",
                        self.old_db_path)
        self.addCleanup(self.tmp.cleanup)

    def _request(self):
        """A fake panel request with no live proxy state."""
        return types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(proxy_state=None)))

    def test_blocked_only_finds_a_run_past_the_first_100(self):
        """Blocked-only lists the blocked Run, oldest of 105."""
        page = panel_module.index(self._request(), blocked="1")
        body = _body(page)
        self.assertIn("/runs/trace-000", body)

    def test_unfiltered_list_shows_at_most_100(self):
        """No filter still shows at most 100 rows plus the header."""
        page = panel_module.index(self._request())
        body = _body(page)
        self.assertEqual(body.count("<tr>"), 101)

    def test_filtered_list_shows_at_most_100(self):
        """A filter that matches all 105 still shows at most 100."""
        page = panel_module.index(self._request(), verdict="Unverified")
        body = _body(page)
        self.assertEqual(body.count("<tr>"), 101)


if __name__ == "__main__":
    unittest.main()
