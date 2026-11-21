"""Bench Part B, outcome-accounting test: the readers must agree on money.

Test-first. Charles reviews this test first. Nothing is fixed until
he does.

Problem in one sentence: the report, the dashboard, the spend cap,
and the per-row price each read the same journal, and they do not
agree on what a Run cost.

Why it matters: the report prints fetch_totals cost_dollars, which
sums only recorded costs. A priced request with no recorded cost
shows $0.00 there. The dashboard prices the same request from the
price table. One Run, two money numbers. The spend cap reads a
third way, through capped_total. A human who checks the money in
two places gets two answers.

Decisions, dated 2026-11-21, made by Charles where noted.
  1. Pin the readers' outputs, not the row's wording. This test
     asserts what the readers conclude, never the outcome or
     close_reason strings on a row. Same rule as the usage
     accounting test.
  2. Patch a test-only price for 127.0.0.1 into the price table.
     The real table holds loopback at $0.00, so a priced check
     would prove nothing without it. Same rule as the usage
     accounting test.
  3. The Red button close row is not pinned here. Claude owns the
     Red button cross-test. This test pins the stop control row
     only, and says nothing about the close row after a Red button
     cut. A stopped attempt priced as a forwarded request is the
     same bug from the accounting side, but the fix belongs to
     the Red button work, not to this test.
  4. The auto-fetch price cache is not pinned here. PRICE_PER_REQUEST
     is still a manual table in code. The AGENTS.md rule says
     auto-fetch published vendor prices into a local cached price
     file, with Unverified as the offline fallback, and no manual
     price tables. That rule is not what the code does today. The
     auto-fetch is a separate build, not this test. This test pins
     the accounting of the prices that exist, not the fetching.

The readers this test goes through, all real, none mirrored:
  - db.fetch_outcome_counts: the outcome buckets
  - db.fetch_totals: attempts, successes, and the recorded cost sum
  - api.stats(): the numbers the dashboard fetches
  - api.summarize_costs and api.attempt_cost: priced cost, row by row
  - api.get_trace(): the per-trace cost and cost line
  - ProxyState.capped_total: the money that feeds the spend cap
  - report.py: the printed cost_dollars line. This test runs
    report.py on the journal and reads what it prints, not what
    fetch_totals returns. fetch_totals sums recorded costs only,
    and the report must not print that sum as the money number.

What the assertions mean in plain words:
  - The buckets add up to the total. Every row lands in exactly
    one bucket on every reader.
  - A Run that did not forward costs $0.00 on every reader. Blocked,
    rate-limited, paused, redirected, stopped, and verified all cost
    nothing, even when the destination has a price.
  - A Run that did forward costs the price on every reader. The
    report, the dashboard, and the spend cap all say the same
    number.
  - A destination with no price stays Unverified. No reader guesses
    a price. The cost line names the destination.
  - A legacy row with no outcome lands in "other" and still counts
    in the total.

What is NOT pinned here, on purpose:
  - Which platform journals a cut as allowed. Same limit as the
    usage accounting test. The readers decide here, so the test
    passes the same way on both once the fix is in. Unverified
    until the platform matrix runs.
  - The dashboard's word for a row. Same limit as the usage
    accounting test. This test pins the dashboard's numbers
    (api.stats), not its words.
  - The auto-fetch price cache. See decision 4 above.

How the close row is awaited, no luck: same rule as the usage
accounting test. The cut tunnel's close row lands on the
handler's own thread after pause_run or redirect_run returns.
The tunnel carries 5 bytes each way before the cut, so the close
row is the Run's row with 5 bytes sent. The test polls for it
with a deadline and fails loudly if it never appears. The dead
check comes second, same reason as there: the cut can reach the
relay two ways, and the dead check must not change what the
handler journals.

Loopback only. Metadata only. No bodies, no keys, no real hostnames.
"""
import contextlib
import io
import os
import re
import socket
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import api as api_module
import db as db_module
import proxy as proxy_module
import report as report_module

# How long the test waits for each step before it fails loudly.
STEP_TIMEOUT = 5
# How long a held step waits for release before it gives up. Longer than
# STEP_TIMEOUT so a slow machine never turns the gate into a false failure.
HOLD_TIMEOUT = 10
# The tunnel carries this payload each way before anything is cut.
PAYLOAD = b"hello"
# Test-only price for one forwarded request to 127.0.0.1. Patched into
# api.PRICE_PER_REQUEST while the readers run. Not a real price.
TEST_PRICE = 0.01

# Human-approved "done" clause for the Verify call in check 1, once per
# task type, as a named read-back, fixed before the Run starts. Copied
# from test_bench_verify_no_trace.py.
DONE_FILE = {
    "task_type": "local file write",
    "clause": "row exists after close/fsync",
    "read_back": "named read-back: file row exists after close/fsync",
    "approved_by": "human",
}


class _HttpEchoHandler(BaseHTTPRequestHandler):
    """Loopback HTTP upstream. Test helper only, never a real destination."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream for CONNECT tunnels. Test helper only."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class OutcomeAccountingTest(unittest.TestCase):
    """The readers must agree on money, for one mixed journal."""

    def setUp(self):
        # A fresh database per test, so every count below is exact.
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        self.echo = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _EchoHandler)
        self.safe = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _EchoHandler)   # where a Redirect sends the Run
        for server in (self.echo, self.safe):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), _HttpEchoHandler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        # High rate limit: rate limiting has its own check, and a 429
        # here would mask the rows this check needs.
        self.proxy = proxy_module.serve(
            "127.0.0.1", 0, self.db_path, rate_limit=10000)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.proxy_port = self.proxy.server_address[1]
        self.dest = f"127.0.0.1:{self.echo.server_address[1]}"
        self.safe_dest = f"127.0.0.1:{self.safe.server_address[1]}"
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"

    def tearDown(self):
        self.proxy.shutdown()
        self.echo.shutdown()
        self.safe.shutdown()
        self.http.shutdown()
        self.proxy.server_close()
        self.echo.server_close()
        self.safe.server_close()
        self.http.server_close()
        self.proxy.state.conn.close()
        self.tmp.cleanup()

    # ---- harness -------------------------------------------------------

    def call(self, url, trace=None):
        """Plain HTTP GET through the proxy. Returns the status code."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": f"http://127.0.0.1:{self.proxy_port}"}))
        req = urllib.request.Request(url, method="GET")
        if trace:
            req.add_header("X-Trace-Id", trace)
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def open_tunnel(self, trace):
        """CONNECT through the proxy for this Run. Returns (sock, status)."""
        sock = socket.create_connection(
            ("127.0.0.1", self.proxy_port), timeout=HOLD_TIMEOUT)
        sock.settimeout(HOLD_TIMEOUT)
        sock.sendall(
            (f"CONNECT {self.dest} HTTP/1.1\r\n"
             f"Host: {self.dest}\r\n"
             f"X-Trace-Id: {trace}\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
        status = int(raw.split(b" ", 2)[1]) if raw else None
        return sock, status

    def open_and_exchange(self, trace):
        """Open a tunnel and carry PAYLOAD each way. Returns the socket."""
        sock, status = self.open_tunnel(trace)
        self.assertEqual(status, 200, "harness: the tunnel did not open")
        sock.sendall(PAYLOAD)
        got = b""
        while len(got) < len(PAYLOAD):
            chunk = sock.recv(65536)
            if not chunk:
                break
            got += chunk
        self.assertEqual(got, PAYLOAD, "harness: the tunnel did not echo")
        return sock

    def assert_tunnel_dead(self, sock):
        """A cut tunnel carries no more bytes: EOF or error, never echo.

        Same rule as the usage accounting test. A graceful hangup reads
        b"" (EOF), an abrupt one raises OSError, and on Linux the first
        send into a just-shut socket can still succeed, so the receive
        side decides. A timeout means still alive.
        """
        sock.settimeout(STEP_TIMEOUT)
        try:
            sock.sendall(b"ping-after-cut")
        except socket.timeout:
            self.fail("tunnel send timed out: the tunnel is still alive")
        except OSError:
            return
        try:
            data = sock.recv(65536)
        except socket.timeout:
            self.fail("tunnel recv timed out: the tunnel is still alive")
        except OSError:
            return
        self.assertEqual(data, b"", "the cut tunnel carries bytes anyway")

    def rows_for(self, trace):
        """Every journal row for one Run, oldest first, as dicts. Read only."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM attempts WHERE trace_id = ? ORDER BY id",
                (trace,))]
        finally:
            conn.close()

    def all_rows(self):
        """Every journal row, oldest first, as dicts. Read only."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM attempts ORDER BY id")]
        finally:
            conn.close()

    def wait_for_close_row(self, trace):
        """Wait until the tunnel's close row is in the journal.

        The close row is the Run's row that carries PAYLOAD. Control rows
        carry no bytes. Polls with a deadline and fails loudly.
        """
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            if any(r["bytes_tx"] == len(PAYLOAD) for r in self.rows_for(trace)):
                return
            time.sleep(0.02)
        self.fail(
            "the handler never journaled a close row that carries the "
            "tunnel's bytes: it was not logged, or it lost the bytes")

    def run_report(self):
        """Run report.py on this journal and return its money number.

        Reads what the report prints, not what fetch_totals returns.
        Returns the cost_dollars value as a float, or None when the
        report says unknown.
        """
        argv = ["report.py", "--db", self.db_path]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(buf):
                report_module.main()
        match = re.search(r"cost_dollars=([0-9.]+|unknown)", buf.getvalue())
        self.assertTrue(match, "harness: the report printed no cost line")
        value = match.group(1)
        return None if value == "unknown" else float(value)

    def read_everything(self, capped_dest=None):
        """Run every reader on the journal, with a test-only price in place.

        Returns a plain dict so the assertions below read in plain words.
        The report reader is report.py's printed cost_dollars line.
        The capped total is read for capped_dest, which defaults to
        the tunnel dest: pass the HTTP dest when the priced row is a
        plain HTTP GET.
        """
        with mock.patch.dict(
                api_module.PRICE_PER_REQUEST, {"127.0.0.1": TEST_PRICE}), \
                mock.patch.object(api_module, "DB_PATH", self.db_path):
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                counts = db_module.fetch_outcome_counts(conn)
                totals = db_module.fetch_totals(conn)
            finally:
                conn.close()
            rows = self.all_rows()
            summary = api_module.summarize_costs(rows)
            return {
                "rows": rows,
                "counts": counts,
                "total": totals["attempts"] or 0,
                "successes": totals["successes"] or 0,
                "report_cost": self.run_report(),
                "dashboard": api_module.stats(),
                "row_costs": [api_module.attempt_cost(r) for r in rows],
                "run_cost": summary["cost"],
                "wasted": summary["wasted"],
                "capped": self.proxy.state.capped_total(
                    capped_dest or self.dest),
            }

    def assert_reconciles(self, seen):
        """The buckets add up to the total, on the db reader and the report."""
        buckets = sum(seen["counts"].values())
        self.assertEqual(
            buckets, seen["total"],
            "the outcome buckets do not add up to the total attempts "
            "(db.fetch_outcome_counts)")
        self.assertEqual(
            seen["total"], len(seen["rows"]),
            "the total attempts does not match the journal rows "
            "(db.fetch_totals)")

    # ---- the checks ----------------------------------------------------

    def test_mixed_journal_reconciles_and_prices_right(self):
        """Check 1: one of each outcome, one priced request, readers agree."""
        # One allowed request that forwards and finishes by itself.
        self.assertEqual(
            self.call(f"{self.base}/echo", "bench-acct-allowed"), 200,
            "harness: the allowed request did not forward")
        # One blocked request that never forwards.
        self.assertEqual(
            self.call("http://exfil.example.test/echo", "bench-acct-blocked"),
            403, "harness: the blocked request was not refused")
        # One tunnel cut by Pause.
        sock_c = self.open_and_exchange("bench-acct-paused")
        try:
            self.assertTrue(
                self.proxy.state.pause_run("bench-acct-paused"),
                "pause_run refused the Run")
            self.wait_for_close_row("bench-acct-paused")
            self.assert_tunnel_dead(sock_c)
        finally:
            sock_c.close()
        # One tunnel cut by Redirect.
        sock_d = self.open_and_exchange("bench-acct-redirected")
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(
                    "bench-acct-redirected", self.safe_dest),
                "redirect_run refused the Run")
            self.wait_for_close_row("bench-acct-redirected")
            self.assert_tunnel_dead(sock_d)
        finally:
            sock_d.close()
        # One Verify call. It journals verified rows for its own Run.
        verdict, _card = self.proxy.state.verify_read_back(
            "bench-acct-verified",
            {"kind": "remote", "system": "stripe",
             "reference": "pi_test_123",
             "idempotency_key": "bench-acct-verify-1"},
            done_clause=DONE_FILE)
        self.assertEqual(verdict, "Unverified",
                         "harness: the Verify call did not run")
        # The priced row is the plain HTTP GET, so the capped total is
        # read for the HTTP dest, not the tunnel dest.
        http_dest = f"127.0.0.1:{self.http.server_address[1]}"
        seen = self.read_everything(capped_dest=http_dest)
        # Reconciliation: the buckets add up to the total.
        self.assert_reconciles(seen)
        # Exact buckets. The Redirect leaves two redirected rows: its
        # control row and its cut close row. The Pause leaves two
        # paused rows the same way: its control row ("human pause")
        # and its cut close row ("tunnel cut by pause"). The Verify
        # leaves two verified rows: its call row and its verdict row.
        self.assertEqual(seen["counts"]["allowed"], 1,
                         "only the finished request counts as allowed")
        self.assertEqual(seen["counts"]["blocked"], 1,
                         "only the refused request counts as blocked")
        self.assertEqual(seen["counts"]["paused"], 2,
                         "the Pause control row and close row count")
        self.assertEqual(seen["counts"]["redirected"], 2,
                         "the Redirect control row and close row count")
        self.assertEqual(seen["counts"]["verified"], 2,
                         "the Verify call row and verdict row count")
        self.assertEqual(seen["successes"], 1,
                         "only the finished request counts as a success")
        # Money: the one forwarded request costs the price on every
        # reader. Everything else costs $0.00.
        self.assertEqual(
            [r for r in seen["row_costs"] if r != 0.0], [TEST_PRICE],
            "exactly one row costs money, the forwarded request")
        self.assertEqual(seen["run_cost"], TEST_PRICE,
                         "the Run's money spent is not the price "
                         "(api.summarize_costs)")
        self.assertEqual(seen["wasted"], 0.0,
                         "nothing was wasted: the refused and cut Runs "
                         "cost $0.00 (api.summarize_costs)")
        self.assertEqual(seen["dashboard"]["total_cost"], TEST_PRICE,
                         "the dashboard total is not the price (api.stats)")
        self.assertEqual(seen["dashboard"]["wasted_cost"], 0.0,
                         "the dashboard wasted cost is not $0.00 (api.stats)")
        self.assertEqual(seen["capped"], TEST_PRICE,
                         "the spend cap total is not the price "
                         "(ProxyState.capped_total)")
        # The report and the dashboard must say the same number.
        # Fails today: the report sums recorded costs only, so it says
        # $0.00 while the dashboard says the price.
        self.assertEqual(
            seen["report_cost"], seen["dashboard"]["total_cost"],
            "the report money and the dashboard money disagree "
            "(report.py vs api.stats)")

    def test_stopped_rows_cost_nothing(self):
        """Check 2: a Red button stop row costs $0.00, and it reconciles."""
        trace = "bench-acct-stopped"
        sock = self.open_and_exchange(trace)
        try:
            self.assertTrue(
                self.proxy.state.red_button("human red button"),
                "red_button refused")
            # The stop control row lands synchronously. The cut close
            # row lands later on the handler thread.
            self.wait_for_close_row(trace)
            self.assert_tunnel_dead(sock)
        finally:
            sock.close()
        seen = self.read_everything()
        self.assert_reconciles(seen)
        stopped = [r for r in seen["rows"] if r["outcome"] == "stopped"]
        self.assertTrue(stopped, "no stopped row was journaled")
        self.assertEqual(
            [api_module.attempt_cost(r) for r in stopped], [0.0] * len(stopped),
            "a stopped row costs money (api.attempt_cost)")
        # The close row after a Red button cut is NOT pinned here.
        # Claude owns the Red button cross-test. See decision 3.

    def test_rate_limited_rows_cost_nothing(self):
        """Check 3: 429 rows cost $0.00, and they reconcile."""
        burst_db = os.path.join(self.tmp.name, "burst.db")
        burst = proxy_module.serve(
            "127.0.0.1", 0, burst_db, rate_limit=5, rate_window=1.0)
        burst_port = burst.server_address[1]
        threading.Thread(target=burst.serve_forever, daemon=True).start()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": f"http://127.0.0.1:{burst_port}"}))
            statuses = []
            for i in range(7):
                req = urllib.request.Request(
                    f"{self.base}/echo", method="GET")
                req.add_header("X-Trace-Id", f"bench-acct-burst-{i}")
                try:
                    with opener.open(req, timeout=10) as resp:
                        statuses.append(resp.status)
                except urllib.error.HTTPError as exc:
                    statuses.append(exc.code)
            self.assertEqual(statuses.count(200), 5,
                             "harness: the burst did not forward five")
            self.assertEqual(statuses.count(429), 2,
                             "harness: the burst did not limit two")
            with mock.patch.dict(
                    api_module.PRICE_PER_REQUEST,
                    {"127.0.0.1": TEST_PRICE}), \
                    mock.patch.object(api_module, "DB_PATH", burst_db):
                conn = sqlite3.connect(burst_db)
                conn.row_factory = sqlite3.Row
                try:
                    counts = db_module.fetch_outcome_counts(conn)
                    totals = db_module.fetch_totals(conn)
                    rows = [dict(r) for r in conn.execute(
                        "SELECT * FROM attempts ORDER BY id")]
                finally:
                    conn.close()
                summary = api_module.summarize_costs(rows)
                dashboard = api_module.stats()
            self.assertEqual(
                sum(counts.values()), totals["attempts"],
                "the outcome buckets do not add up to the total attempts")
            self.assertEqual(counts["allowed"], 5)
            self.assertEqual(counts["rate_limited"], 2)
            self.assertEqual(
                [api_module.attempt_cost(r) for r in rows
                 if r["outcome"] == "rate_limited"], [0.0, 0.0],
                "a rate-limited row costs money (api.attempt_cost)")
            self.assertEqual(summary["cost"], 5 * TEST_PRICE,
                             "the five forwarded requests do not cost "
                             "five prices (api.summarize_costs)")
            self.assertEqual(summary["wasted"], 0.0,
                             "the limited rows wasted money "
                             "(api.summarize_costs)")
            self.assertEqual(dashboard["total_cost"], 5 * TEST_PRICE,
                             "the dashboard total is not five prices "
                             "(api.stats)")
        finally:
            burst.shutdown()
            burst.server_close()
            burst.state.conn.close()

    def test_unknown_destination_stays_unverified(self):
        """Check 4: a destination with no price is never guessed."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        conn = db_module.init_db(tmp.name)
        db_module.log_attempt(
            conn, trace_id="bench-acct-unknown", task_number=1, attempt=1,
            status="success", dest_host="pay.example.com",
            endpoint="/v1/x", method="POST", outcome="allowed")
        db_module.log_attempt(
            conn, trace_id="bench-acct-unknown-blocked", task_number=1,
            attempt=1, status="blocked", dest_host="example.com",
            endpoint="/", method="GET", outcome="blocked",
            reason="not_allowlisted")
        conn.close()
        with mock.patch.object(api_module, "DB_PATH", tmp.name):
            conn = sqlite3.connect(tmp.name)
            conn.row_factory = sqlite3.Row
            try:
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM attempts ORDER BY id")]
            finally:
                conn.close()
            summary = api_module.summarize_costs(rows)
            dashboard = api_module.stats()
            trace = api_module.get_trace("bench-acct-unknown")
        allowed_row = [r for r in rows if r["outcome"] == "allowed"][0]
        self.assertIsNone(
            api_module.attempt_cost(allowed_row),
            "the unpriced destination got a price (api.attempt_cost)")
        self.assertIsNone(
            summary["cost"],
            "the total is a number while a destination has no price "
            "(api.summarize_costs)")
        self.assertEqual(summary["wasted"], 0.0,
                         "the blocked row wasted money "
                         "(api.summarize_costs)")
        self.assertIsNone(dashboard["total_cost"],
                          "the dashboard total is a number while a "
                          "destination has no price (api.stats)")
        self.assertEqual(dashboard["unknown_runs"], 1,
                         "the dashboard does not count the unknown run "
                         "(api.stats)")
        self.assertIn("pay.example.com", dashboard["cost_line"],
                      "the cost line does not name the unpriced "
                      "destination (api.stats)")
        self.assertIsNone(trace["cost"],
                          "the trace cost is a number while its "
                          "destination has no price (api.get_trace)")
        self.assertIn("pay.example.com", trace["cost_line"],
                      "the trace cost line does not name the unpriced "
                      "destination (api.get_trace)")

    def test_legacy_row_lands_in_other_and_counts(self):
        """Check 5: a row with no outcome is 'other', and it still counts."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        conn = db_module.init_db(tmp.name)
        db_module.log_attempt(
            conn, trace_id="bench-acct-legacy-allowed", task_number=1,
            attempt=1, status="success", dest_host="127.0.0.1",
            endpoint="/echo", method="GET", outcome="allowed")
        db_module.log_attempt(
            conn, trace_id="bench-acct-legacy-blocked", task_number=1,
            attempt=1, status="blocked", dest_host="example.com",
            endpoint="/", method="GET", outcome="blocked",
            reason="not_allowlisted")
        db_module.log_attempt(
            conn, trace_id="bench-acct-legacy-old", task_number=1,
            attempt=1, status="success", dest_host="127.0.0.1",
            endpoint="/echo", method="GET", outcome="")
        conn.close()
        with mock.patch.object(api_module, "DB_PATH", tmp.name):
            conn = sqlite3.connect(tmp.name)
            conn.row_factory = sqlite3.Row
            try:
                counts = db_module.fetch_outcome_counts(conn)
                totals = db_module.fetch_totals(conn)
            finally:
                conn.close()
            dashboard = api_module.stats()
        self.assertEqual(counts["other"], 1,
                         "the legacy row did not land in 'other' "
                         "(db.fetch_outcome_counts)")
        self.assertEqual(
            sum(counts.values()), totals["attempts"],
            "the outcome buckets do not add up to the total attempts "
            "(db.fetch_outcome_counts)")
        # The fix adds "other" to /stats, so the sum below counts all
        # nine buckets. Before the fix it counted eight, and the sum
        # fell one short of the total.
        dashboard_buckets = (
            dashboard["allowed"] + dashboard["blocked"]
            + dashboard["rate_limited"] + dashboard["paused"]
            + dashboard["redirected"] + dashboard["stopped"]
            + dashboard["verified"] + dashboard["warned"]
            + dashboard["other"])
        self.assertEqual(
            dashboard_buckets, dashboard["total_attempts"],
            "the dashboard buckets do not add up to the total attempts "
            "(api.stats has no 'other')")


if __name__ == "__main__":
    unittest.main()
