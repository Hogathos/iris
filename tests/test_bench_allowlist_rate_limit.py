"""Bench cross-test: allowlist and rate limit, together, on both paths.

M4 (tests/test_m4.py) pins each gate alone: a non-allowlisted
destination gets 403 and is never forwarded, a burst past the limit
gets 429 and recovers after the window. What M4 never pins is how the
two gates behave together, on both the plain HTTP path and the
CONNECT path, and what the readers see after a mixed run.

This test pins, with real proxy traffic:

  1. A host:port allowlist entry, on both paths: the exact port
     forwards, another port on the same host is blocked.
  2. CONNECT rate limiting: a burst of tunnels past the limit gets
     429, no tunnel opens, and the next tunnel opens after the window.
  3. The rate limit is per destination: a burst to one upstream does
     not slow another upstream.
  4. The readers agree after a mixed run: the buckets reconcile, the
     dashboard agrees, the spend cap counts allowed rows only, and
     every non-forwarded row costs $0.00.
  5. Refusal precedence and attempt numbering: the rate check runs
     before the pause check, the allowlist check runs before the
     rate check, and a rate-limited arrival does not consume an
     attempt number.

What this test does not pin, on purpose: a redirect changing the
destination the rate limit keys on (Steer Redirect owns that
interaction, and it needs its own cross-test), and the spend cap
interacting with the rate limit (the cap has its own bench test).

The checks run in alphabetical order, on shared fixtures, so the
readers check (4) sees the rows that checks 1 to 3 journalled. The
precedence check (5) runs last because it journals a pause control
row, which would pollute the readers check.
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

PAYLOAD = b"bench-ping"
STEP_TIMEOUT = 5.0
WINDOW_SLEEP = 1.2  # past the 1s rate window, inside the 10s retry window


class _FrozenClock:
    """A clock the test moves by hand, so the rate window never moves
    on its own. Test helper only."""

    def __init__(self):
        self.now_seconds = time.time()

    def now(self):
        return self.now_seconds

    def advance(self, seconds):
        self.now_seconds += seconds


class _HttpEchoHandler(BaseHTTPRequestHandler):
    """Loopback HTTP upstream. Counts hits, so a blocked request that
    reached the upstream would show up. Test helper only."""

    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream for CONNECT tunnels. Counts connections,
    so a refused tunnel that opened anyway would show up. Test helper
    only."""

    connections = 0

    def handle(self):
        type(self).connections += 1
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class AllowlistRateLimitBenchTest(unittest.TestCase):
    """The two M4 gates, together, on both paths, through real traffic."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        _HttpEchoHandler.hits = 0
        _EchoHandler.connections = 0
        cls.http_a = ThreadingHTTPServer(("127.0.0.1", 0), _HttpEchoHandler)
        cls.http_b = ThreadingHTTPServer(("127.0.0.1", 0), _HttpEchoHandler)
        for server in (cls.http_a, cls.http_b):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.tcp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        threading.Thread(target=cls.tcp.serve_forever, daemon=True).start()
        cls.port_a = cls.http_a.server_address[1]
        cls.port_b = cls.http_b.server_address[1]
        cls.tcp_port = cls.tcp.server_address[1]
        cls.base_a = f"http://127.0.0.1:{cls.port_a}"
        cls.base_b = f"http://127.0.0.1:{cls.port_b}"
        cls.tcp_dest = f"127.0.0.1:{cls.tcp_port}"
        # Default policy proxy: loopback allowlist, generous rate limit.
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.proxy = proxy_module.serve("127.0.0.1", 0, cls.db_path, rate_limit=10000)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        # Burst proxy: 5 requests per 1s window, like the M4 demo limit.
        # The window runs on a frozen clock the test moves by hand, so
        # a slow runner can never stretch a burst past the window.
        cls.burst_clock = _FrozenClock()
        cls.burst_db = os.path.join(cls.tmp.name, "burst.db")
        cls.burst = proxy_module.serve(
            "127.0.0.1", 0, cls.burst_db, rate_limit=5, rate_window=1.0, rate_clock=cls.burst_clock.now
        )
        cls.burst_port = cls.burst.server_address[1]
        threading.Thread(target=cls.burst.serve_forever, daemon=True).start()
        # Host:port proxy: only one exact port on loopback is allowlisted.
        cls.port_db = os.path.join(cls.tmp.name, "port.db")
        cls.port_proxy = proxy_module.serve(
            "127.0.0.1", 0, cls.port_db, rate_limit=10000, allowlist=[f"127.0.0.1:{cls.port_a}"]
        )
        cls.port_proxy_port = cls.port_proxy.server_address[1]
        threading.Thread(target=cls.port_proxy.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.port_proxy, cls.burst, cls.proxy, cls.http_a, cls.http_b, cls.tcp):
            server.shutdown()
            server.server_close()
        for server in (cls.port_proxy, cls.burst, cls.proxy):
            server.state.conn.close()
        cls.tmp.cleanup()

    # ---- harness -------------------------------------------------------

    def rows(self, db_path, trace=None):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        out = conn.execute("SELECT * FROM attempts ORDER BY id").fetchall()
        conn.close()
        if trace is not None:
            out = [r for r in out if r["trace_id"] == trace]
        return out

    def wait_for_rows(self, db_path, trace, count):
        """Poll the journal until this trace has count rows, or fail."""
        deadline = time.time() + STEP_TIMEOUT
        while time.time() < deadline:
            found = self.rows(db_path, trace)
            if len(found) >= count:
                return found
            time.sleep(0.05)
        self.fail(f"harness: trace {trace} has {len(self.rows(db_path, trace))} rows, wanted {count}")

    def call(self, url, proxy_port, trace=None):
        """Plain HTTP GET through the proxy. Returns the status code."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy_port}"}))
        req = urllib.request.Request(url, method="GET")
        if trace:
            req.add_header("X-Trace-Id", trace)
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def connect(self, dest, proxy_port, trace):
        """Raw CONNECT through the proxy. Returns (sock, status)."""
        sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=STEP_TIMEOUT)
        sock.settimeout(STEP_TIMEOUT)
        sock.sendall((f"CONNECT {dest} HTTP/1.1\r\nHost: {dest}\r\nX-Trace-Id: {trace}\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
        status = int(raw.split(b" ", 2)[1]) if raw else None
        return sock, status

    def connect_and_exchange(self, dest, proxy_port, trace):
        """Open a tunnel and carry PAYLOAD each way. Returns the socket."""
        sock, status = self.connect(dest, proxy_port, trace)
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

    def run_report(self, db_path):
        """Run report.py on a journal and return its money number."""
        argv = ["report.py", "--db", db_path]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(buf):
                report_module.main()
        match = re.search(r"cost_dollars=([0-9.]+|unknown)", buf.getvalue())
        self.assertTrue(match, "harness: the report printed no cost line")
        value = match.group(1)
        return None if value == "unknown" else float(value)

    def read_journal(self, db_path, state, capped_dests):
        """Run every reader on one journal. Returns a plain dict."""
        with mock.patch.object(api_module, "DB_PATH", db_path):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                counts = db_module.fetch_outcome_counts(conn)
                totals = db_module.fetch_totals(conn)
            finally:
                conn.close()
            rows = self.rows(db_path)
            summary = api_module.summarize_costs(rows)
            return {
                "counts": counts,
                "total": totals["attempts"] or 0,
                "report_cost": self.run_report(db_path),
                "dashboard": api_module.stats(),
                "run_cost": summary["cost"],
                "wasted": summary["wasted"],
                "capped": {d: state.capped_total(d) for d in capped_dests},
            }

    # ---- checks ----------------------------------------------------------

    def test_connect_rate_limit_then_recovers(self):
        """A burst of tunnels past the limit gets 429, then recovers.

        M4 pins the 429 on plain HTTP only. The CONNECT path has its
        own rate check, and it needs its own pin: the 6th tunnel must
        not open, and the next tunnel must open after the window.
        """
        trace = "bench-connect-burst"
        sockets = []
        try:
            for _ in range(5):
                sockets.append(self.connect_and_exchange(self.tcp_dest, self.burst_port, trace))
            sock, status = self.connect(self.tcp_dest, self.burst_port, trace)
            self.assertEqual(status, 429, "the 6th tunnel must not open")
            sock.close()
            self.burst_clock.advance(WINDOW_SLEEP)  # past the 1s window
            sockets.append(self.connect_and_exchange(self.tcp_dest, self.burst_port, trace))
        finally:
            for sock in sockets:
                sock.close()
        rows = self.wait_for_rows(self.burst_db, trace, 7)
        # The five tunnels stay open while the 6th is refused, and a
        # tunnel's row lands only when it closes. So the refusal row
        # sorts before the five close rows: assert the counts, not the
        # order. The recovery tunnel's 200 already proves it opened.
        self.assertEqual(
            sorted(r["outcome"] for r in rows),
            ["allowed"] * 6 + ["rate_limited"],
            "the burst rows must be 6 allowed and 1 rate_limited",
        )
        limited = [r for r in rows if r["outcome"] == "rate_limited"]
        self.assertEqual(len(limited), 1)
        limited = limited[0]
        self.assertEqual(limited["status"], "rate_limited")
        self.assertEqual(limited["reason"], "rate_limited")
        self.assertEqual((limited["bytes_in"], limited["bytes_out"]), (0, 0))
        self.assertEqual(limited["method"], "CONNECT")

    def test_host_port_allowlist_entry_both_paths(self):
        """A host:port entry forwards its port only, on both paths.

        The default allowlist is bare loopback hosts, which match any
        port. A host:port entry matches one exact port. That shape is
        pinned only at is_allowed unit level today; this check pins it
        through real traffic, on the plain HTTP path and the CONNECT
        path, and it journals the blocked rows for the readers check.
        """
        trace = "bench-host-port"
        hits_before = _HttpEchoHandler.hits
        conns_before = _EchoHandler.connections
        # The exact port forwards, on both paths.
        self.assertEqual(self.call(self.base_a, self.port_proxy_port, trace), 200)
        sock, status = self.connect(f"127.0.0.1:{self.port_a}", self.port_proxy_port, trace)
        self.assertEqual(status, 200, "the exact port must tunnel")
        sock.close()
        # Another port on the same host is blocked, on both paths, and
        # the blocked request never reaches the upstream.
        self.assertEqual(self.call(self.base_b, self.port_proxy_port, trace), 403)
        sock, status = self.connect(f"127.0.0.1:{self.port_b}", self.port_proxy_port, trace)
        self.assertEqual(status, 403, "the other port must not tunnel")
        sock.close()
        self.assertEqual(_HttpEchoHandler.hits, hits_before + 1)
        self.assertEqual(_EchoHandler.connections, conns_before)
        rows = self.wait_for_rows(self.port_db, trace, 4)
        self.assertEqual(
            [r["outcome"] for r in rows],
            ["allowed", "allowed", "blocked", "blocked"],
            "the host:port rows must read allowed, blocked",
        )
        for row in rows[2:]:
            self.assertEqual(row["status"], "blocked")
            self.assertEqual(row["reason"], "not_allowlisted")
            self.assertEqual((row["bytes_in"], row["bytes_out"]), (0, 0))
        # The plain loopback allowlist, on the default proxy: a
        # non-allowlisted name is blocked on both paths, an allowlisted
        # one forwards. These rows feed the default journal.
        default_trace = "bench-default-blocked"
        self.assertEqual(self.call("http://exfil.example.test/echo", self.proxy_port, default_trace), 403)
        sock, status = self.connect("exfil.example.test:443", self.proxy_port, default_trace)
        self.assertEqual(status, 403, "the CONNECT tunnel must not open")
        sock.close()
        self.assertEqual(self.call(self.base_a, self.proxy_port, default_trace), 200)
        default_rows = self.wait_for_rows(self.db_path, default_trace, 3)
        self.assertEqual(
            [r["outcome"] for r in default_rows],
            ["blocked", "blocked", "allowed"],
            "the default rows must read blocked, blocked, allowed",
        )
        blocked_connect = default_rows[1]
        self.assertEqual(blocked_connect["method"], "CONNECT")
        self.assertEqual(blocked_connect["endpoint"], "exfil.example.test:443")

    def test_rate_limit_is_per_destination(self):
        """A burst to one upstream does not slow another upstream.

        The rate window is keyed by destination label. Five GETs to
        upstream A fill A's window; upstream B still answers, and the
        6th GET to A is the one that gets 429.
        """
        trace = "bench-per-dest"
        for _ in range(5):
            self.assertEqual(self.call(self.base_a, self.burst_port, trace), 200)
        self.assertEqual(self.call(self.base_b, self.burst_port, trace), 200, "the burst to A must not slow B")
        self.assertEqual(self.call(self.base_a, self.burst_port, trace), 429, "the 6th GET to A must be rate limited")
        rows = self.rows(self.burst_db, trace)
        self.assertEqual(
            [r["outcome"] for r in rows],
            ["allowed"] * 5 + ["allowed"] + ["rate_limited"],
            "the per-destination rows must read allowed, rate_limited",
        )

    def test_readers_reconcile_on_both_journals(self):
        """The readers agree after the mixed run, on every journal.

        Burst journal: 6 allowed tunnels and 6 allowed GETs, plus 2
        rate-limited rows. Default journal: 1 allowed GET, 2 blocked
        rows. Host:port journal: 2 allowed, 2 blocked. The buckets
        must add up on each one, the dashboard must agree, the spend
        cap must count allowed rows only, and every non-forwarded row
        must cost $0.00.
        """
        burst = self.read_journal(self.burst_db, self.burst.state, [self.base_a[7:], self.base_b[7:], self.tcp_dest])
        self.assertEqual(burst["counts"]["allowed"], 12)
        self.assertEqual(burst["counts"]["rate_limited"], 2)
        self.assertEqual(burst["counts"]["other"], 0)
        self.assertEqual(sum(burst["counts"].values()), burst["total"])
        self.assertEqual(burst["total"], 14)
        self.assertEqual(burst["dashboard"]["total_attempts"], 14)
        self.assertEqual(burst["dashboard"]["rate_limited"], 2)
        self.assertEqual(burst["report_cost"], 0.0)
        self.assertEqual(burst["run_cost"], 0.0)
        self.assertEqual(burst["wasted"], 0.0)
        self.assertEqual(burst["dashboard"]["total_cost"], 0.0)
        self.assertEqual(burst["dashboard"]["unknown_runs"], 0)
        for dest, total in burst["capped"].items():
            self.assertEqual(total, 0.0, f"the cap for {dest} must be $0.00")

        default = self.read_journal(self.db_path, self.proxy.state, [self.base_a[7:]])
        self.assertEqual(default["counts"]["allowed"], 1)
        self.assertEqual(default["counts"]["blocked"], 2)
        self.assertEqual(sum(default["counts"].values()), default["total"])
        self.assertEqual(default["total"], 3)
        self.assertEqual(default["dashboard"]["total_attempts"], 3)
        self.assertEqual(default["report_cost"], 0.0)
        self.assertEqual(default["run_cost"], 0.0)
        self.assertEqual(default["dashboard"]["total_cost"], 0.0)

        port = self.read_journal(self.port_db, self.port_proxy.state, [self.base_a[7:]])
        self.assertEqual(port["counts"]["allowed"], 2)
        self.assertEqual(port["counts"]["blocked"], 2)
        self.assertEqual(sum(port["counts"].values()), port["total"])
        self.assertEqual(port["total"], 4)
        self.assertEqual(port["dashboard"]["total_attempts"], 4)
        self.assertEqual(port["report_cost"], 0.0)
        self.assertEqual(port["run_cost"], 0.0)

    def test_refusal_precedence_and_attempt_numbers(self):
        """The gates refuse in order, and a refusal takes no number.

        Gate order on both paths is allowlist, then rate limit, then
        pause. So a paused Run over the limit still gets 429, not 503,
        and a non-allowlisted name over the limit still gets 403, not
        429. A rate-limited arrival logs a peeked attempt number, so
        the next forwarded request keeps the same number.
        """
        trace = "bench-precedence"
        self.burst_clock.advance(WINDOW_SLEEP)  # clear the A window from the burst check
        for _ in range(5):
            self.assertEqual(self.call(self.base_a, self.burst_port, trace), 200)
        self.assertEqual(self.call(self.base_a, self.burst_port, trace), 429)
        self.assertTrue(self.burst.state.pause_run(trace), "harness: the pause did not land")
        self.assertEqual(
            self.call(self.base_a, self.burst_port, trace), 429, "the rate check runs before the pause check: 429, not 503"
        )
        rows = self.wait_for_rows(self.burst_db, trace, 8)
        self.assertEqual(rows[-1]["outcome"], "rate_limited")
        self.assertEqual(rows[-1]["status"], "rate_limited")

        # A non-allowlisted name over the limit still gets 403. Real
        # traffic cannot fill its window, because a blocked request
        # never reaches the rate check, so the window is filled here
        # with direct calls instead.
        label = "exfil.example.test:80"
        filled = [self.proxy.state.check_rate(label) for _ in range(5)]
        self.assertEqual(filled, [True] * 5, "harness: the window is full")
        blocked_trace = "bench-precedence-blocked"
        self.assertEqual(
            self.call("http://exfil.example.test/echo", self.proxy_port, blocked_trace),
            403,
            "the allowlist check runs before the rate check: 403, not 429",
        )
        blocked_rows = self.wait_for_rows(self.db_path, blocked_trace, 1)
        self.assertEqual(blocked_rows[0]["outcome"], "blocked")

        # A rate-limited arrival takes no attempt number: the next
        # forwarded request on the same trace keeps the same number.
        retry_trace = "bench-attempt-number"
        self.burst_clock.advance(WINDOW_SLEEP)  # clear the A window from the pause check
        for _ in range(5):
            self.assertEqual(self.call(self.base_a, self.burst_port, retry_trace), 200)
        self.assertEqual(self.call(self.base_a, self.burst_port, retry_trace), 429)
        # Past the rate window, inside the retry window: the advance
        # moves the rate clock only, the retry window reads real time
        # and the next GET follows at once.
        self.burst_clock.advance(WINDOW_SLEEP)
        self.assertEqual(self.call(self.base_a, self.burst_port, retry_trace), 200)
        retry_rows = self.wait_for_rows(self.burst_db, retry_trace, 7)
        limited = retry_rows[5]
        allowed = retry_rows[6]
        self.assertEqual(limited["outcome"], "rate_limited")
        self.assertEqual(allowed["status"], "success")
        self.assertEqual(limited["attempt"], allowed["attempt"], "the rate-limited arrival must not consume an attempt number")
        self.assertEqual([r["status"] for r in retry_rows[5:]], ["rate_limited", "success"])


if __name__ == "__main__":
    unittest.main()
