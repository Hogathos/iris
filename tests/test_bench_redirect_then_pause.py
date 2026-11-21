"""Cross-test 2: redirect then pause.

Test only. Charles reviews it first. A fix prompt follows only if a check
fails. Nothing is fixed here.

This is the second file in the cross-test queue, the reverse order of
test_bench_pause_then_redirect.py. It was started on "continue", with no
prompt of its own. The rule it pins is my reading of two decisions Charles
already made, and he should confirm it:
  - 2026-11-21 02:19 EST, ALLOW: steering a steered Run is allowed, and the
    Run flows to the new destination after Verify and retry.
  - Filed 2026-11-21: a panel that shows REDIRECTED for a held Run would
    mislead a human. The panel must show PAUSED while the Run is held.
Pause is the stop button, so it must work on a redirected Run. The
redirect stays held through the pause and through the resume.

What the tests pin, in plain words:
  1. Pause holds a redirected Run. A Run is redirected, a new tunnel flows
     to the NEW destination, then Pause cuts that tunnel. The Run is
     paused. The redirect is still held and saved. The pause row names the
     new destination. A new tunnel gets a 503. The new destination
     received only the bytes sent before the cut. (Passes today.)
  2. Control. Pause on a Run that was never redirected holds it and leaves
     no redirect, so check 1's redirect assertions are not vacuous.
     (Passes today.)
  3. Resume lands on the new destination. After Verify and retry the Run
     flows again, to the NEW destination, and the redirect is still held.
     The old destination received nothing after the redirect. (Passes
     today.)
  4. The panel. Redirect then pause folds to PAUSED, never REDIRECTED,
     because the Run is held. After Verify and retry it folds to FLOWING.
     One variant uses hand-built rows, one uses the real rows of the real
     sequence. (Expected to FAIL today. See below.)

What a scratch run showed today: the proxy does all of checks 1 to 3. The
panel does not do check 4. devpanel states has a transition from FLOWING on
a pause and none from REDIRECTED on a pause, so a pause on a redirected Run
is ignored. The panel keeps showing REDIRECTED while the proxy answers 503.
That is the same mislead the pause-then-redirect file guards against, in
the other order. The likely fix is one transition in states.py, from
REDIRECTED on a pause to PAUSED. That is for a separate prompt.

Not pinned here: the Verify row folds as a resume before the retry, so the
panel shows FLOWING one step early. That question is open in the
pause-then-redirect file and waits for Charles.

Loopback only. Metadata only. No bodies, no keys, no real hostnames.
"""
import os
import socket
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import db as db_module
import proxy as proxy_module
from devpanel import states as states_module

# How long the test waits for each step before it fails loudly.
STEP_TIMEOUT = 5
HOLD_TIMEOUT = 10
# Each tunnel carries this payload each way before anything is steered.
PAYLOAD = b"hello"
# What a human Verify hands to verify_run. The read back is truthy.
DONE_CLAUSE = {"approved_by": "human", "read_back": {"ok": True}}


class _CountingEchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream that counts the bytes it received.

    Test helper only. It counts bytes, never stores them.
    """

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            with self.server.count_lock:
                self.server.bytes_in += len(data)
            self.request.sendall(data)


def _start_counting_echo():
    server = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), _CountingEchoHandler)
    server.bytes_in = 0
    server.count_lock = threading.Lock()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def journal_row(outcome, reason, status="unknown"):
    """A journal row as the proxy writes it, with the fields the panel reads."""
    return {"outcome": outcome, "reason": reason, "status": status}


class RedirectThenPauseTest(unittest.TestCase):
    """Pausing a Run that is already redirected."""

    def setUp(self):
        # A fresh database per test, so every row read below is exact.
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        self.old_dest_server = _start_counting_echo()   # where the Run was
        self.new_dest_server = _start_counting_echo()   # where it is sent
        # High rate limit: rate limiting has its own tests, and a 429
        # here would mask the tunnel this test needs to open.
        self.proxy = proxy_module.serve(
            "127.0.0.1", 0, self.db_path, rate_limit=10000)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.proxy_port = self.proxy.server_address[1]
        self.dest = f"127.0.0.1:{self.old_dest_server.server_address[1]}"
        self.safe_dest = f"127.0.0.1:{self.new_dest_server.server_address[1]}"

    def tearDown(self):
        self.proxy.shutdown()
        self.old_dest_server.shutdown()
        self.new_dest_server.shutdown()
        self.proxy.server_close()
        self.old_dest_server.server_close()
        self.new_dest_server.server_close()
        self.proxy.state.conn.close()
        self.tmp.cleanup()

    # ---- harness -------------------------------------------------------

    def open_tunnel(self, trace):
        """CONNECT through the proxy for this Run. Returns (sock, status).

        The client always asks for the OLD destination. Where the tunnel
        really goes is the proxy's call, and that is what these tests read.
        """
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
        """A cut tunnel carries no more bytes: EOF or error, never echo."""
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

    def saved_redirects(self):
        """The redirects saved for a restart, as the proxy reloads them."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return db_module.load_redirected(conn)
        finally:
            conn.close()

    def wait_for_row(self, trace, wanted, what):
        """Wait until a journal row matches. Poll with a deadline, fail loud."""
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            rows = [r for r in self.rows_for(trace) if wanted(r)]
            if rows:
                return rows
            time.sleep(0.02)
        self.fail(f"the journal never got {what}")

    def received(self):
        """Bytes each destination really received, from the servers."""
        with self.old_dest_server.count_lock:
            old = self.old_dest_server.bytes_in
        with self.new_dest_server.count_lock:
            new = self.new_dest_server.bytes_in
        return old, new

    def redirect_a_live_run(self, trace):
        """Redirect a Run with a live tunnel, then flow to the new place.

        Opens a tunnel to the old destination, redirects the Run (which
        cuts that tunnel), waits for the cut tunnel's close row, then opens
        a second tunnel that must flow to the NEW destination. Returns the
        second socket, so the caller closes it.
        """
        first = self.open_and_exchange(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest),
                "redirect_run refused a live Run")
            self.wait_for_row(
                trace,
                lambda r: r["reason"] == "tunnel cut by redirect",
                "the redirect-cut tunnel's close row")
            self.assert_tunnel_dead(first)
        finally:
            first.close()
        second = self.open_and_exchange(trace)
        old_bytes, new_bytes = self.received()
        self.assertEqual(
            (old_bytes, new_bytes), (len(PAYLOAD), len(PAYLOAD)),
            "harness: the second tunnel did not flow to the new destination")
        return second

    def verify_then_retry(self, trace):
        """A human Verify, then a retry. Both must succeed."""
        self.assertTrue(
            self.proxy.state.verify_run(trace, DONE_CLAUSE),
            "harness: a human Verify on a paused Run was refused")
        self.assertEqual(
            self.proxy.state.retry_run(trace),
            (True, "retry allowed after verify"),
            "harness: retry after a fresh Verify was refused")

    def pause_the_redirected_run(self, trace):
        """Redirect, flow, then Pause. Returns once the journal is settled."""
        sock = self.redirect_a_live_run(trace)
        try:
            self.assertTrue(
                self.proxy.state.pause_run(trace),
                "pause_run refused a redirected Run")
            self.wait_for_row(
                trace,
                lambda r: r["reason"] == "tunnel cut by pause",
                "the pause-cut tunnel's close row")
            self.assert_tunnel_dead(sock)
        finally:
            sock.close()

    def resumed_tunnel(self, trace):
        """Carry PAYLOAD through a fresh tunnel and return its close row.

        Found by destination and bytes, never by first match: the row that
        makes the count of allowed close rows for the NEW destination go up.
        """
        def close_rows():
            return [r for r in self.rows_for(trace)
                    if r["outcome"] == "allowed"
                    and r["dest_host"] == self.safe_dest
                    and r["bytes_tx"] == len(PAYLOAD)]
        seen = len(close_rows())
        sock = self.open_and_exchange(trace)
        sock.close()
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            rows = close_rows()
            if len(rows) > seen:
                return rows[-1]
            time.sleep(0.02)
        self.fail("the journal never got the resumed tunnel's close row")

    # ---- check 1 -------------------------------------------------------

    def test_pause_on_a_redirected_run_holds_it(self):
        """Check 1: paused, redirect still held, 503, nothing leaks."""
        trace = "bench-redirect-then-pause-1"
        self.pause_the_redirected_run(trace)
        self.assertTrue(
            self.proxy.state.is_paused(trace), "the Run is not paused")
        self.assertEqual(
            self.proxy.state._redirects[trace]["dest_host"], self.safe_dest,
            "the pause dropped the redirect held in memory")
        self.assertEqual(
            self.saved_redirects()[trace]["dest_host"], self.safe_dest,
            "the pause dropped the redirect saved for a restart")
        rows = self.rows_for(trace)
        pause_rows = [r for r in rows if r["reason"] == "human pause"]
        self.assertEqual(
            [r["dest_host"] for r in pause_rows], [self.safe_dest],
            "the pause row must name the new destination, where the cut "
            "tunnel was going")
        cut = [r for r in rows if r["reason"] == "tunnel cut by pause"]
        self.assertEqual(
            [(r["outcome"], r["dest_host"], r["bytes_tx"], r["bytes_rx"])
             for r in cut],
            [("paused", self.safe_dest, len(PAYLOAD), len(PAYLOAD))],
            "the cut tunnel's close row must read paused and keep its bytes")
        held_sock, held_status = self.open_tunnel(trace)
        held_sock.close()
        self.assertEqual(
            held_status, 503, "a paused and redirected Run must hold")
        old_bytes, new_bytes = self.received()
        self.assertEqual(
            (old_bytes, new_bytes), (len(PAYLOAD), len(PAYLOAD)),
            "bytes reached a destination after the pause")

    # ---- check 2 -------------------------------------------------------

    def test_pause_on_a_run_that_was_never_redirected(self):
        """Check 2, the control: Pause alone holds and leaves no redirect."""
        trace = "bench-redirect-then-pause-control-1"
        sock = self.open_and_exchange(trace)
        try:
            self.assertTrue(self.proxy.state.pause_run(trace))
            self.wait_for_row(
                trace,
                lambda r: r["reason"] == "tunnel cut by pause",
                "the cut tunnel's close row")
            self.assert_tunnel_dead(sock)
        finally:
            sock.close()
        self.assertTrue(self.proxy.state.is_paused(trace))
        self.assertNotIn(trace, self.proxy.state._redirects)
        self.assertNotIn(trace, self.saved_redirects())
        held_sock, held_status = self.open_tunnel(trace)
        held_sock.close()
        self.assertEqual(held_status, 503)

    # ---- check 3 -------------------------------------------------------

    def test_resume_after_redirect_then_pause_lands_on_the_new_destination(self):
        """Check 3: Verify then retry, and traffic goes to the new place."""
        trace = "bench-redirect-then-pause-resume-1"
        self.pause_the_redirected_run(trace)
        self.verify_then_retry(trace)
        self.assertFalse(
            self.proxy.state.is_paused(trace),
            "the Run is still paused after Verify and retry")
        self.assertEqual(
            self.proxy.state._redirects[trace]["dest_host"], self.safe_dest,
            "the resume dropped the redirect")
        resumed = self.resumed_tunnel(trace)
        self.assertEqual(
            (resumed["bytes_tx"], resumed["bytes_rx"]),
            (len(PAYLOAD), len(PAYLOAD)))
        old_bytes, new_bytes = self.received()
        self.assertEqual(
            old_bytes, len(PAYLOAD),
            "the old destination received bytes after the redirect")
        self.assertEqual(
            new_bytes, 2 * len(PAYLOAD),
            "the resumed tunnel did not reach the new destination")

    # ---- check 4 -------------------------------------------------------

    def test_panel_folds_redirect_then_pause_to_paused(self):
        """Check 4, hand-built rows in the shapes the proxy writes."""
        redirect = journal_row("redirected", "human redirect")
        redirect_cut = journal_row("redirected", "tunnel cut by redirect")
        pause = journal_row("paused", "human pause")
        pause_cut = journal_row("paused", "tunnel cut by pause")
        held_attempt = journal_row("paused", "human pause")
        verify = journal_row("", "verify ok")
        resume = journal_row("", "retry allowed after verify")
        self.assertEqual(
            states_module.run_state([redirect, redirect_cut]), "REDIRECTED",
            "harness: a redirected Run that is not paused is REDIRECTED")
        self.assertEqual(
            states_module.run_state(
                [redirect, redirect_cut, pause, pause_cut]), "PAUSED",
            "a Run that is redirected and then paused is held, so the "
            "panel must say PAUSED, not REDIRECTED")
        self.assertEqual(
            states_module.run_state(
                [redirect, redirect_cut, pause, pause_cut, held_attempt]),
            "PAUSED",
            "a held attempt must not change the run state")
        self.assertEqual(
            states_module.run_state(
                [redirect, redirect_cut, pause, pause_cut, held_attempt,
                 verify, resume]),
            "FLOWING",
            "after Verify and retry the Run flows")

    def test_panel_folds_the_real_rows_of_redirect_then_pause(self):
        """Check 4, the same fold over rows the real proxy wrote."""
        trace = "bench-redirect-then-pause-panel-1"
        self.pause_the_redirected_run(trace)
        held_sock, held_status = self.open_tunnel(trace)
        held_sock.close()
        self.assertEqual(held_status, 503, "harness: the Run was not held")
        self.verify_then_retry(trace)
        rows = self.rows_for(trace)
        redirect_at = [i for i, r in enumerate(rows)
                       if r["reason"] == "human redirect"][0]
        held_at = [i for i, r in enumerate(rows)
                   if r["http_status"] == 503][0]
        pause_at = [i for i, r in enumerate(rows)
                    if r["reason"] == "tunnel cut by pause"][0]
        self.assertEqual(
            states_module.run_state(rows[:redirect_at + 1]), "REDIRECTED",
            "harness: the real rows of a redirect fold to REDIRECTED")
        self.assertEqual(
            states_module.run_state(rows[:pause_at + 1]), "PAUSED",
            "the real rows of redirect then pause must fold to PAUSED")
        self.assertEqual(
            states_module.run_state(rows[:held_at + 1]), "PAUSED",
            "a real held attempt must not change the run state")
        self.assertEqual(
            states_module.run_state(rows), "FLOWING",
            "the real rows after Verify and retry must fold to FLOWING")


if __name__ == "__main__":
    unittest.main()
