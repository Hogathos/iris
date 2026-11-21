"""Bench cross-test: the plain HTTP path, through the proxy.

M1 and test_proxy.py pin the plain HTTP handler one behavior at a
time: trace forwarding, framing refusals, failure status, usage
tokens. What no test pins is the plain HTTP path as a whole, through
real proxy traffic: a body forwarded intact, a big response streamed,
an upstream that is down, a framing refusal, how the gates meet plain
HTTP, and what the readers say after the mixed run.

This test pins, with real proxy traffic:

  1. A big response streams: the upstream returns more than
     MAX_BUFFERED_RESPONSE_BYTES, the client gets every byte, and
     the row lands after the stream ends with the right byte counts.
  2. A framing refusal: two Content-Length headers get 400, and the
     row says outcome failure with the right reason.
  3. An upstream that is down: the client gets 502, and the row
     says status error, outcome allowed, zero bytes out. The outcome
     is the gate decision, not the result: the request passed the
     gates, the upstream failed.
  4. The gates on plain HTTP: a paused trace gets 503, and a spend
     cap warns once at 80% then blocks at 100% with 503.
  5. A POST body forwards intact: the upstream gets the exact bytes,
     the client gets the exact echo, and the row counts the bytes.
  6. The readers agree after the mixed run, on both journals: the
     buckets reconcile, the dashboard agrees, the spend cap counts
     allowed rows only, and the cost is what was actually spent.
  7. The warn threshold rounds to cents: a 5-cent cap warns at 4
     cents on plain HTTP, and the same threshold on CONNECT warns
     the same way.

One rule the readers check pins: a control row is not spend. The
cap warn row carries the cumulative total that tripped it, and the
cap set row carries no cost at all. Neither row is a request, so
neither may add to the total. The total must be the allowed rows
only.

What this test does not pin, on purpose: the allowlist 403 on plain
HTTP (the allowlist bench test owns it), a redirect changing the
plain HTTP destination (that interaction needs its own cross-test),
the 413 body_too_big refusal (it needs a 10 MB body), a chunked POST
body, and HEAD, PUT, PATCH, DELETE, OPTIONS (they share the same
_handle path as GET and POST).

The checks run in alphabetical order, on shared fixtures, so the
readers check (6) runs after checks 1 to 5 and sees every row they
journalled. The float check (7) runs its own proxy on a fresh
journal, so its rows cannot touch the readers check.
"""

import contextlib
import http.client
import io
import os
import re
import socket
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

STEP_TIMEOUT = 5.0
TEST_PRICE = 0.20  # twenty cents per request, patched in for the cap
TEST_CAP = 1.00  # five priced requests, then the cap blocks
FLOAT_PRICE = 0.01  # one cent per request, patched in for the float cap
FLOAT_CAP = 0.05  # five priced requests reach 80%, then the cap blocks
BIG_SIZE = 1024 * 1024 + 65536  # past MAX_BUFFERED_RESPONSE_BYTES
BIG_BODY = (bytes(range(256)) * (BIG_SIZE // 256 + 1))[:BIG_SIZE]
POST_BODY = b'{"hello": "plain-http"}'


class _HttpMainHandler(BaseHTTPRequestHandler):
    """Loopback HTTP upstream for the main proxy. Test helper only."""

    def do_GET(self):
        if self.path == "/echo":
            body = b"ok"
        elif self.path == "/big":
            body = BIG_BODY
        else:
            body = None
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/post-echo":
            self.send_response(404)
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _HttpEchoHandler(BaseHTTPRequestHandler):
    """Loopback HTTP upstream for the gates proxy. Test helper only."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _free_port():
    """A loopback port with nothing listening on it. Test helper only."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class PlainHttpBenchTest(unittest.TestCase):
    """The plain HTTP path, end to end, through real proxy traffic."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "main.db")
        cls.gates_db = os.path.join(cls.tmp.name, "gates.db")
        db_module.init_db(cls.db_path)
        db_module.init_db(cls.gates_db)

        cls.http_main = ThreadingHTTPServer(
            ("127.0.0.1", 0), _HttpMainHandler)
        cls.http_a = ThreadingHTTPServer(
            ("127.0.0.1", 0), _HttpEchoHandler)
        cls.http_b = ThreadingHTTPServer(
            ("127.0.0.1", 0), _HttpEchoHandler)
        for server in (cls.http_main, cls.http_a, cls.http_b):
            threading.Thread(target=server.serve_forever,
                             daemon=True).start()
        cls.main_port = cls.http_main.server_address[1]
        cls.port_a = cls.http_a.server_address[1]
        cls.port_b = cls.http_b.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.main_port}"
        cls.base_a = f"http://127.0.0.1:{cls.port_a}"
        cls.base_b = f"http://127.0.0.1:{cls.port_b}"
        cls.dest_main = f"127.0.0.1:{cls.main_port}"
        cls.dest_a = f"127.0.0.1:{cls.port_a}"
        cls.dest_b = f"127.0.0.1:{cls.port_b}"
        cls.closed_port = _free_port()
        cls.closed_dest = f"127.0.0.1:{cls.closed_port}"
        cls.closed_url = f"http://127.0.0.1:{cls.closed_port}/echo"

        cls.proxy = proxy_module.serve(
            "127.0.0.1", 0, cls.db_path, rate_limit=10000)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        cls.gates = proxy_module.serve(
            "127.0.0.1", 0, cls.gates_db, rate_limit=10000)
        cls.gates_port = cls.gates.server_address[1]
        threading.Thread(target=cls.gates.serve_forever, daemon=True).start()
        time.sleep(0.5)  # let both proxies bind before the first call

    @classmethod
    def tearDownClass(cls):
        for server in (cls.proxy, cls.gates,
                       cls.http_main, cls.http_a, cls.http_b):
            server.shutdown()
            server.server_close()
        cls.proxy.state.conn.close()
        cls.gates.state.conn.close()
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
        self.fail(f"harness: trace {trace} has "
                  f"{len(self.rows(db_path, trace))} rows, "
                  f"wanted {count}")

    def call(self, url, proxy_port, trace=None):
        """Plain HTTP GET through the proxy. Returns the status code."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": f"http://127.0.0.1:{proxy_port}"}))
        req = urllib.request.Request(url, method="GET")
        if trace:
            req.add_header("X-Trace-Id", trace)
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def call_get_body(self, url, proxy_port, trace=None):
        """Plain HTTP GET through the proxy. Returns (status, body)."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": f"http://127.0.0.1:{proxy_port}"}))
        req = urllib.request.Request(url, method="GET")
        if trace:
            req.add_header("X-Trace-Id", trace)
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def call_post(self, url, proxy_port, body, trace=None):
        """Plain HTTP POST through the proxy. Returns (status, body)."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": f"http://127.0.0.1:{proxy_port}"}))
        req = urllib.request.Request(url, data=body, method="POST")
        if trace:
            req.add_header("X-Trace-Id", trace)
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def call_duplicate_content_length(self, url, proxy_port, trace):
        """Raw GET with two Content-Length headers. Returns the status."""
        conn = http.client.HTTPConnection(
            "127.0.0.1", proxy_port, timeout=10)
        try:
            conn.putrequest("GET", url, skip_host=True)
            conn.putheader("Host", f"127.0.0.1:{proxy_port}")
            conn.putheader("Content-Length", "5")
            conn.putheader("Content-Length", "5")
            conn.putheader("X-Trace-Id", trace)
            conn.endheaders()
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()

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

    def read_journal(self, db_path, state, capped_dests, patched_price=None):
        """Run every reader on one journal. Returns a plain dict.

        The price patch, when given, covers the reads too: the
        readers price rows from the same table the proxy priced
        traffic with, so a patched read is the only honest one.
        """
        patch = (mock.patch.dict(api_module.PRICE_PER_REQUEST,
                                 {"127.0.0.1": patched_price})
                 if patched_price is not None
                 else contextlib.nullcontext())
        with patch:
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
                    "capped": {d: state.capped_total(d)
                               for d in capped_dests},
                }

    # ---- checks ----------------------------------------------------------

    def test_big_response_streams(self):
        """A response past the buffer limit streams, whole and counted.

        The upstream returns more than MAX_BUFFERED_RESPONSE_BYTES,
        so the proxy streams it in chunks instead of buffering it.
        The client must get every byte, and the row must land after
        the stream ends with the right byte counts.
        """
        trace = "bench-plain-big"
        status, body = self.call_get_body(
            f"{self.base}/big", self.proxy_port, trace)
        self.assertEqual(status, 200, "the big response must arrive")
        self.assertEqual(body, BIG_BODY, "the streamed bytes must match")
        rows = self.wait_for_rows(self.db_path, trace, 1)
        row = rows[0]
        self.assertEqual(
            (row["outcome"], row["status"], row["http_status"]),
            ("allowed", "success", 200),
            "the streamed row must read allowed and success")
        self.assertEqual(row["bytes_out"], len(BIG_BODY),
                         "the streamed row must count every byte")
        self.assertEqual(row["bytes_rx"], len(BIG_BODY))

    def test_duplicate_content_length_refused(self):
        """Two Content-Length headers get 400, never forwarded.

        One length, one framing, never both: a repeat is refused
        even when the two values agree, so a smuggled length cannot
        slip past the single read. The row must say outcome failure.
        """
        trace = "bench-plain-dup-cl"
        status = self.call_duplicate_content_length(
            f"{self.base}/echo", self.proxy_port, trace)
        self.assertEqual(status, 400, "the duplicate length must be refused")
        rows = self.wait_for_rows(self.db_path, trace, 1)
        row = rows[0]
        self.assertEqual(row["outcome"], "failure")
        self.assertEqual(row["reason"], "duplicate_content_length")
        self.assertEqual(row["http_status"], 400)

    def test_failed_upstream_gets_502(self):
        """An upstream that is down gets 502, and the row says why.

        The outcome is the gate decision, not the result: the request
        passed the gates, so it reads allowed, and the failure reads
        status error with zero bytes out. The client sees 502.
        """
        trace = "bench-plain-down"
        status = self.call(self.closed_url, self.proxy_port, trace)
        self.assertEqual(status, 502, "the dead upstream must give 502")
        rows = self.wait_for_rows(self.db_path, trace, 1)
        row = rows[0]
        self.assertEqual(
            (row["outcome"], row["status"], row["http_status"]),
            ("allowed", "error", 502),
            "the dead-upstream row must read allowed, error, 502")
        self.assertEqual(row["bytes_out"], 0)
        self.assertEqual(row["close_reason"], "upstream_reset")

    def test_gates_pause_and_cap_on_plain_http(self):
        """Pause and cap meet plain HTTP: 503, warn once, then block.

        A paused trace gets 503 on plain HTTP, and other traces
        still flow. A spend cap on plain HTTP warns once at 80%,
        then blocks at 100% with 503. The warn row carries the
        cumulative total that tripped it.
        """
        cap_trace = "bench-plain-cap"
        pause_trace = "bench-plain-pause"
        with mock.patch.dict(api_module.PRICE_PER_REQUEST,
                            {"127.0.0.1": TEST_PRICE}):
            self.assertTrue(
                self.gates.state.set_cap(
                    self.dest_a, TEST_CAP, reason="human cap"),
                "harness: the cap did not land")
            for _ in range(5):
                self.assertEqual(
                    self.call(self.base_a, self.gates_port, cap_trace), 200)
            cap_rows = self.wait_for_rows(self.gates_db, cap_trace, 6)
            warns = [r for r in cap_rows if r["outcome"] == "warned"]
            self.assertEqual(len(warns), 1, "the cap must warn exactly once")
            self.assertEqual(warns[0]["reason"], "cap warn 80%")
            self.assertEqual(warns[0]["cost_dollars"], 0.80,
                             "the warn row carries the spent total")
            self.assertEqual(
                [r["outcome"] for r in cap_rows],
                ["allowed"] * 4 + ["warned", "allowed"],
                "the cap rows must read allowed, warned, allowed")
            self.assertEqual(
                self.call(self.base_a, self.gates_port, cap_trace), 503,
                "the 6th request past the cap must be blocked")
            cap_rows = self.wait_for_rows(self.gates_db, cap_trace, 7)
            self.assertEqual(
                [r["outcome"] for r in cap_rows],
                ["allowed"] * 4 + ["warned", "allowed"] + ["blocked"],
                "the cap rows must end with the blocked refusal")

            self.assertEqual(
                self.call(self.base_b, self.gates_port, pause_trace), 200)
            self.assertTrue(
                self.gates.state.pause_run(pause_trace),
                "harness: the pause did not land")
            self.assertEqual(
                self.call(self.base_b, self.gates_port, pause_trace), 503,
                "the paused trace must get 503 on plain HTTP")
            pause_rows = self.wait_for_rows(self.gates_db, pause_trace, 3)
            self.assertEqual(
                [r["outcome"] for r in pause_rows],
                ["allowed", "paused", "paused"],
                "the pause rows must read allowed, paused, paused")
            self.assertEqual(pause_rows[1]["reason"], "human pause")

    def test_warn_threshold_rounds_to_cents(self):
        """A 5-cent cap warns at 4 cents on plain HTTP, not never.

        0.8 times 5 cents is a hair above 4 cents in float, so an
        unrounded 80% threshold is unreachable before the cap
        blocks. The threshold is rounded to cents, on this path and
        on CONNECT. Runs its own proxy: the gates proxy already
        carries twenty cents of spend on its pause destination, and
        a fresh cap needs a fresh destination.
        """
        trace = "bench-plain-float"
        tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmp.name, "float.db")
        db_module.init_db(db_path)
        float_proxy = None
        try:
            float_proxy = proxy_module.serve(
                "127.0.0.1", 0, db_path, rate_limit=10000)
            float_port = float_proxy.server_address[1]
            threading.Thread(target=float_proxy.serve_forever,
                             daemon=True).start()
            with mock.patch.dict(api_module.PRICE_PER_REQUEST,
                                {"127.0.0.1": FLOAT_PRICE}):
                self.assertTrue(
                    float_proxy.state.set_cap(
                        self.dest_b, FLOAT_CAP, reason="human cap"),
                    "harness: the cap did not land")
                for _ in range(5):
                    self.assertEqual(
                        self.call(self.base_b, float_port, trace), 200)
                rows = self.wait_for_rows(db_path, trace, 6)
                warns = [r for r in rows if r["outcome"] == "warned"]
                self.assertEqual(
                    len(warns), 1, "the cap must warn exactly once")
                self.assertEqual(warns[0]["reason"], "cap warn 80%")
                self.assertEqual(warns[0]["cost_dollars"], 0.04,
                                 "the warn row carries the spent total")
                self.assertEqual(
                    [r["outcome"] for r in rows],
                    ["allowed"] * 4 + ["warned", "allowed"],
                    "the cap rows must read allowed, warned, allowed")
                self.assertEqual(
                    self.call(self.base_b, float_port, trace), 503,
                    "the 6th request past the cap must be blocked")
                rows = self.wait_for_rows(db_path, trace, 7)
                self.assertEqual(
                    [r["outcome"] for r in rows],
                    ["allowed"] * 4 + ["warned", "allowed"] + ["blocked"],
                    "the cap rows must end with the blocked refusal")
        finally:
            if float_proxy is not None:
                float_proxy.shutdown()
                float_proxy.server_close()
                float_proxy.state.conn.close()
            tmp.cleanup()

    def test_post_body_forwards_intact(self):
        """A POST body crosses the proxy byte for byte, both ways.

        The upstream gets the exact bytes the client sent, the client
        gets the exact echo back, and the row counts the bytes in and
        out. A plain GET first proves the path flows before the POST.
        """
        trace = "bench-plain-post"
        status, body = self.call_get_body(
            f"{self.base}/echo", self.proxy_port, trace)
        self.assertEqual(status, 200, "the GET control must flow")
        self.assertEqual(body, b"ok")
        status, echoed = self.call_post(
            f"{self.base}/post-echo", self.proxy_port, POST_BODY, trace)
        self.assertEqual(status, 200, "the POST must flow")
        self.assertEqual(echoed, POST_BODY,
                         "the echo must match the sent body")
        rows = self.wait_for_rows(self.db_path, trace, 2)
        post_row = [r for r in rows if r["method"] == "POST"][0]
        self.assertEqual(
            (post_row["outcome"], post_row["status"]),
            ("allowed", "success"),
            "the POST row must read allowed and success")
        self.assertEqual(post_row["bytes_in"], len(POST_BODY))
        self.assertEqual(post_row["bytes_out"], len(POST_BODY))

    def test_readers_reconcile_on_both_journals(self):
        """The readers agree after the mixed run, on both journals.

        Main journal: 4 allowed rows and 1 other row. The dead
        upstream row costs $0.00 on loopback, so the total is $0.00.
        Gates journal: 6 allowed rows, 1 warned, 1 blocked, 2
        paused, 1 other. The real spend is the 6 allowed rows at
        twenty cents each, $1.20, and nothing was wasted. A control row
        is not spend, so the warn row and the cap set row must add
        nothing to the total.
        """
        main = self.read_journal(
            self.db_path, self.proxy.state,
            [self.dest_main, self.closed_dest])
        self.assertEqual(main["counts"]["allowed"], 4)
        self.assertEqual(main["counts"]["other"], 1)
        self.assertEqual(sum(main["counts"].values()), main["total"])
        self.assertEqual(main["total"], 5)
        self.assertEqual(main["dashboard"]["total_attempts"], 5)
        self.assertEqual(main["report_cost"], 0.0)
        self.assertEqual(main["run_cost"], 0.0)
        self.assertEqual(main["wasted"], 0.0)
        self.assertEqual(main["dashboard"]["total_cost"], 0.0)
        self.assertEqual(main["dashboard"]["unknown_runs"], 0)
        for dest, total in main["capped"].items():
            self.assertEqual(total, 0.0, f"the cap for {dest} must be $0.00")

        gates = self.read_journal(
            self.gates_db, self.gates.state,
            [self.dest_a, self.dest_b], patched_price=TEST_PRICE)
        self.assertEqual(gates["counts"]["allowed"], 6)
        self.assertEqual(gates["counts"]["warned"], 1)
        self.assertEqual(gates["counts"]["blocked"], 1)
        self.assertEqual(gates["counts"]["paused"], 2)
        self.assertEqual(gates["counts"]["other"], 1)
        self.assertEqual(sum(gates["counts"].values()), gates["total"])
        self.assertEqual(gates["total"], 11)
        self.assertEqual(gates["dashboard"]["total_attempts"], 11)
        self.assertEqual(gates["report_cost"], 1.20,
                         "the report must say what was actually spent")
        self.assertEqual(gates["run_cost"], 1.20)
        self.assertEqual(gates["wasted"], 0.0)
        self.assertEqual(gates["dashboard"]["total_cost"], 1.20)
        self.assertEqual(gates["capped"][self.dest_a], 1.00)
        self.assertEqual(gates["capped"][self.dest_b], 0.20)
