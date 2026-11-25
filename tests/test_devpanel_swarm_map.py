"""Swarm map 2D test (test-first, failing until built).

Problem in one sentence: the dev panel shows Runs as a table, and a
human cannot see the swarm at a glance, which Runs called which
destinations and when.

Done clause for this test Run (human-approved, once per task type, as
a named read-back, before the Run starts):
  task_type = "local file write"
  clause    = "row exists after close/fsync"
  read_back = "named read-back: file row exists after close/fsync"
No approved done = Unverified, never Correct.

Expected failure right now: there is no devpanel.map module and no
/map route. That failure is correct. It is the test-first proof.
Charles reviews this test with the finished work, because he chose
the build ("I can live with this").

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
from devpanel import map as map_module
from devpanel import panel as panel_module


def _row(trace_id, dest, outcome="", status="success",
         latency=0.1, created_at="2026-11-22T06:00:00+00:00",
         reason=""):
    """One hand-built journal row, in the shape the proxy writes."""
    return {
        "trace_id": trace_id, "dest_host": dest, "endpoint": dest,
        "method": "CONNECT", "outcome": outcome, "status": status,
        "latency_seconds": latency, "created_at": created_at,
        "reason": reason, "bytes_in": 0, "bytes_out": 0,
    }


class SwarmMapLayoutTest(unittest.TestCase):
    """The layout is a pure function of the journal rows."""

    def test_same_journal_same_places(self):
        """Same rows in, same node places out, every time."""
        traces = [
            {"trace_id": "run-a", "rows": [
                _row("run-a", "127.0.0.1:9001",
                     created_at="2026-11-22T06:00:00+00:00")]},
            {"trace_id": "run-b", "rows": [
                _row("run-b", "127.0.0.1:9002",
                     created_at="2026-11-22T06:01:00+00:00")]},
        ]
        first = map_module.build_map(traces)
        second = map_module.build_map(traces)
        self.assertEqual(
            [(n["id"], n["x"], n["y"]) for n in first["nodes"]],
            [(n["id"], n["x"], n["y"]) for n in second["nodes"]],
            "the map must be reproducible: same journal, same places")

    def test_no_two_nodes_share_a_place(self):
        """Two Runs at the same second on the same destination."""
        traces = [
            {"trace_id": "run-a", "rows": [
                _row("run-a", "127.0.0.1:9001")]},
            {"trace_id": "run-b", "rows": [
                _row("run-b", "127.0.0.1:9001")]},
        ]
        built = map_module.build_map(traces)
        places = [(n["x"], n["y"]) for n in built["nodes"]]
        self.assertEqual(
            len(places), len(set(places)),
            "each node keeps its own lane, even at the same second")

    def test_run_node_links_to_run_page(self):
        """Clicking a Run node opens that Run's page."""
        traces = [{"trace_id": "run-a", "rows": [
            _row("run-a", "127.0.0.1:9001")]}]
        built = map_module.build_map(traces)
        run_node = [n for n in built["nodes"]
                    if n["kind"] == "run"][0]
        self.assertEqual(run_node["href"], "/runs/run-a")
        self.assertEqual(run_node["label"], "Run run-a")

    def test_dest_node_links_to_filtered_list(self):
        """Clicking a destination node filters the run list to it."""
        traces = [{"trace_id": "run-a", "rows": [
            _row("run-a", "127.0.0.1:9001")]}]
        built = map_module.build_map(traces)
        dest_node = [n for n in built["nodes"]
                     if n["kind"] == "dest"][0]
        self.assertEqual(dest_node["href"], "/?dest=127.0.0.1%3A9001")

    def test_edge_colors_follow_outcomes(self):
        """Green allowed, red blocked or failed, amber rate-limited."""
        traces = [
            {"trace_id": "run-a", "rows": [
                _row("run-a", "127.0.0.1:9001", status="success")]},
            {"trace_id": "run-b", "rows": [
                _row("run-b", "127.0.0.1:9002", outcome="blocked",
                     status="failure")]},
            {"trace_id": "run-c", "rows": [
                _row("run-c", "127.0.0.1:9003",
                     outcome="rate_limited", status="failure")]},
        ]
        built = map_module.build_map(traces)
        by_dest = {e["to"]: e for e in built["edges"]}
        self.assertEqual(by_dest["dest:127.0.0.1:9001"]["color"], "green")
        self.assertEqual(by_dest["dest:127.0.0.1:9002"]["color"], "red")
        self.assertEqual(by_dest["dest:127.0.0.1:9003"]["color"], "amber")

    def test_edge_label_counts_calls_and_seconds(self):
        """One edge per Run and destination, with count and seconds."""
        traces = [{"trace_id": "run-a", "rows": [
            _row("run-a", "127.0.0.1:9001", latency=0.2),
            _row("run-a", "127.0.0.1:9001", latency=0.3)]}]
        built = map_module.build_map(traces)
        self.assertEqual(len(built["edges"]), 1)
        self.assertEqual(built["edges"][0]["label"], "2 calls, 0.5s")

    def test_cap_twenty_runs(self):
        """The map shows the latest 20 Runs, never more."""
        traces = [
            {"trace_id": f"run-{i:02d}", "rows": [
                _row(f"run-{i:02d}", "127.0.0.1:9001",
                     created_at=f"2026-11-22T06:{i:02d}:00+00:00")]}
            for i in range(25)]
        built = map_module.build_map(traces)
        run_nodes = [n for n in built["nodes"] if n["kind"] == "run"]
        self.assertEqual(len(run_nodes), 20)


class SwarmMapRouteTest(unittest.TestCase):
    """The /map route serves one inline SVG, zero JavaScript."""

    def setUp(self):
        """Fresh temp DB, seeded with two Runs, panel pointed at it."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        conn = db_module.init_db(self.db_path)
        db_module.log_attempt(
            conn, trace_id="run-a", task_number=1, attempt=1,
            status="success", dest_host="127.0.0.1:9001",
            endpoint="127.0.0.1:9001", method="CONNECT",
            created_at="2026-11-22T06:00:00+00:00")
        db_module.log_attempt(
            conn, trace_id="run-b", task_number=1, attempt=1,
            status="failure", outcome="blocked",
            reason="not allowed", dest_host="127.0.0.1:9002",
            endpoint="127.0.0.1:9002", method="CONNECT",
            created_at="2026-11-22T06:01:00+00:00")
        conn.commit()
        conn.close()
        self.old_db_path = api_module.DB_PATH
        api_module.DB_PATH = self.db_path
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, api_module, "DB_PATH", self.old_db_path)

    def test_map_page_is_svg_without_script(self):
        """One inline SVG, no script tag, node links inside it."""
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(proxy_state=None)))
        page = panel_module.swarm_map(request)
        body = page if isinstance(page, str) else page.body.decode()
        self.assertIn("<svg", body)
        self.assertNotIn("<script", body)
        self.assertIn("/runs/run-a", body)
        self.assertIn("/runs/run-b", body)
        self.assertIn("showing the latest 20 Runs", body)

    def test_index_links_to_map(self):
        """The map route serves the swarm map straight from the panel."""
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(proxy_state=None)))
        page = panel_module.swarm_map(request)
        body = page if isinstance(page, str) else page.body.decode()
        self.assertIn("<svg", body)

    def test_index_dest_filter_reads_every_row(self):
        """A dest filter finds a Run that called it on any row."""
        conn = db_module.init_db(self.db_path)
        db_module.log_attempt(
            conn, trace_id="run-a", task_number=1, attempt=2,
            status="success", dest_host="127.0.0.1:9003",
            endpoint="127.0.0.1:9003", method="CONNECT",
            created_at="2026-11-22T06:02:00+00:00")
        conn.commit()
        conn.close()
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(proxy_state=None)))
        page = panel_module.index(request, dest="127.0.0.1:9001")
        body = page if isinstance(page, str) else page.body.decode()
        self.assertIn("/runs/run-a", body,
                      "run-a called 9001 first, so the 9001 filter "
                      "must list it even though its latest row is 9003")


if __name__ == "__main__":
    unittest.main()
