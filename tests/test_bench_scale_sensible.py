"""Bench: sensible scale fixes — indexes, WAL, backlog.

Covers the three cheap items from the scale audit (2026-11-21):
an index on (trace_id, id), an index on (dest_host, outcome), WAL
journal mode, and a raised listen backlog. No behavior changes.

The checks are the point: the queries must USE the indexes (EXPLAIN
QUERY PLAN shows SEARCH, not SCAN), a committed row must be visible
to a second connection the moment log_attempt returns, and the
backlog must be the value we claim.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

import db as db_module
from proxy import ProxyServer

BACKLOG = 128


class TestScaleSensible(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "scale.db")
        self.conn = db_module.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _log_one(self, trace_id="t-1", dest_host="127.0.0.1",
                 outcome="allowed", status="success"):
        db_module.log_attempt(
            self.conn, trace_id=trace_id, task_number=1, attempt=1,
            status=status, dest_host=dest_host, endpoint="/",
            method="GET", outcome=outcome)

    def test_indexes_exist(self):
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        self.assertIn("idx_attempts_trace_id", names)
        self.assertIn("idx_attempts_dest_outcome", names)

    def test_trace_lookup_uses_index(self):
        for i in range(20):
            self._log_one(trace_id=f"t-{i % 2}")
        plan = "\n".join(r[-1] for r in self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM attempts "
            "WHERE trace_id = ? ORDER BY id DESC LIMIT 1", ("t-1",)))
        self.assertIn("SEARCH", plan)
        self.assertNotIn("SCAN", plan)

    def test_cap_lookup_uses_index(self):
        for i in range(20):
            self._log_one(dest_host=f"127.0.0.{i % 2 + 1}")
        plan = "\n".join(r[-1] for r in self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT outcome, dest_host, cost_dollars "
            "FROM attempts WHERE dest_host = ? AND outcome = 'allowed'",
            ("127.0.0.1",)))
        self.assertIn("SEARCH", plan)
        self.assertNotIn("SCAN", plan)

    def test_wal_mode(self):
        mode = self.conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode, "wal")

    def test_committed_row_visible_at_once(self):
        # Journal-first guard: the moment log_attempt returns, the row
        # must be visible to a second connection. WAL must not delay
        # that. Passes before the fix too; it must keep passing after.
        self._log_one(trace_id="t-visible")
        other = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            rows = other.execute(
                "SELECT trace_id FROM attempts WHERE trace_id = ?",
                ("t-visible",)).fetchall()
        finally:
            other.close()
        self.assertEqual([r[0] for r in rows], ["t-visible"])

    def test_row_survives_reopen(self):
        # Guard: a committed row survives close and reopen, WAL or not.
        self._log_one(trace_id="t-survive")
        self.conn.close()
        reopened = db_module.init_db(self.db_path)
        try:
            rows = reopened.execute(
                "SELECT trace_id FROM attempts WHERE trace_id = ?",
                ("t-survive",)).fetchall()
        finally:
            reopened.close()
        self.assertEqual([r[0] for r in rows], ["t-survive"])
        self.conn = db_module.init_db(self.db_path)

    def test_backlog_raised(self):
        self.assertEqual(ProxyServer.request_queue_size, BACKLOG)


if __name__ == "__main__":
    unittest.main()
