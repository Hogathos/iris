"""Relay timeout control test (test-first, failing until built).

Problem in one sentence: a human must be able to read and change the
silent tunnel cutoff from the dev panel, 5 to 3600 seconds, with bad
values refused and the fired row reading plain words.

Done clause for this test Run (human-approved, once per task type,
as a named read-back, before the Run starts):
  task_type = "local file write"
  clause    = "row exists after close/fsync"
  read_back = "named read-back: file row exists after close/fsync"
No approved done = Unverified, never Correct.

Expected failure right now: ProxyState has no set_relay_timeout, the
proxy has no _timeout_words, and the panel has no relay timeout
route. That failure is correct. It is the test-first proof. Charles
reviews this test with the finished work, because he chose one prompt
for the whole change.

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames.
"""

import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import proxy as proxy_module
from devpanel import panel as panel_module


class RelayTimeoutControlTest(unittest.TestCase):
    """The setter, the wording, and the panel route, in that order."""

    def _serve(self):
        """A live proxy on loopback with a temp journal. Caller closes."""
        tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmp.name, "relay-timeout.db")
        proxy = proxy_module.serve("127.0.0.1", 0, db_path)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        return proxy, tmp, db_path

    def _close(self, proxy, tmp):
        """Stop the proxy and close the journal before the temp dir goes."""
        proxy.shutdown()
        proxy.server_close()
        proxy.state.conn.close()
        tmp.cleanup()

    def _request(self, state):
        """A fake panel request that carries the live proxy state."""
        return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(proxy_state=state)))

    def _timeout_rows(self, db_path):
        """Every journal row the cutoff setter wrote."""
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM attempts WHERE method = 'TIMEOUT'")]
        finally:
            conn.close()

    def test_setter_accepts_value_in_bounds_and_journals_first(self):
        proxy, tmp, db_path = self._serve()
        try:
            state = proxy.state
            self.assertEqual(state.relay_timeout, 30.0)
            self.assertTrue(state.set_relay_timeout(300))
            self.assertEqual(state.relay_timeout, 300.0)
            rows = self._timeout_rows(db_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reason"], "silent cutoff set to 300s")
            self.assertEqual(rows[0]["cost_dollars"], 0.0)
        finally:
            self._close(proxy, tmp)

    def test_setter_refuses_out_of_bounds_and_bad_values(self):
        proxy, tmp, db_path = self._serve()
        try:
            state = proxy.state
            for bad in (0, -5, 4.9, 3601, "abc", None):
                self.assertFalse(state.set_relay_timeout(bad))
            self.assertEqual(state.relay_timeout, 30.0)
            self.assertEqual(self._timeout_rows(db_path), [])
        finally:
            self._close(proxy, tmp)

    def test_setter_accepts_float_and_numeric_string(self):
        proxy, tmp, db_path = self._serve()
        try:
            state = proxy.state
            self.assertTrue(state.set_relay_timeout(30.5))
            self.assertEqual(state.relay_timeout, 30.5)
            self.assertTrue(state.set_relay_timeout("120"))
            self.assertEqual(state.relay_timeout, 120.0)
            rows = self._timeout_rows(db_path)
            self.assertEqual([r["reason"] for r in rows], ["silent cutoff set to 30.5s", "silent cutoff set to 120s"])
        finally:
            self._close(proxy, tmp)

    def test_timeout_words(self):
        self.assertEqual(proxy_module._timeout_words(30), "closed after 30s silent")
        self.assertEqual(proxy_module._timeout_words(0.3), "closed after 0.3s silent")
        self.assertEqual(proxy_module._timeout_words(300), "closed after 300s silent")

    def test_panel_route_sets_cutoff_and_refuses_bad_value(self):
        proxy, tmp, db_path = self._serve()
        try:
            state = proxy.state
            panel_module.app.state.proxy_state = state
            request = self._request(state)
            resp = panel_module.relay_timeout_button(request, seconds="300")
            self.assertEqual(resp.status_code, 303)
            self.assertEqual(state.relay_timeout, 300.0)
            bad = panel_module.relay_timeout_button(request, seconds="2")
            self.assertEqual(state.relay_timeout, 300.0)
            self.assertIn("refused", bad.body.decode().lower())
        finally:
            panel_module.app.state.proxy_state = None
            self._close(proxy, tmp)

    def test_panel_index_has_no_cutoff_form(self):
        """The cutoff left the page: the relay timeout is API-only now."""
        proxy, tmp, db_path = self._serve()
        old_db_path = panel_module.api_module.DB_PATH
        try:
            panel_module.api_module.DB_PATH = db_path
            panel_module.app.state.proxy_state = proxy.state
            html = panel_module.index(self._request(proxy.state))
            self.assertNotIn("Silent tunnel cutoff", html)
            self.assertNotIn('name="seconds"', html)
        finally:
            panel_module.api_module.DB_PATH = old_db_path
            panel_module.app.state.proxy_state = None
            self._close(proxy, tmp)


if __name__ == "__main__":
    unittest.main()
