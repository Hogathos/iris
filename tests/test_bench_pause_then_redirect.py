"""Cross-test 1: pause then redirect, the redirect is ALLOWED.

Test only. Charles reviews it first. A fix prompt follows only if a check
fails. Nothing is fixed here.

Record correction. The first version of this file pinned refusal and said
the rule was decided by Charles, locked. That was wrong. Charles never
decided refusal. The word came from a queue label in the usage-accounting
prompt, and the brief for the first version then called it a locked
decision. Charles, 2026-11-21 02:17 to 02:19 EST: "I don't remember
saying anything about refusing." That version and its refusal rule are
void. They are not carried forward.

Rule this file pins, decided by Charles 2026-11-21 02:19 EST, locked:
ALLOW. A redirect on a paused Run is allowed. The Run stays paused until
a human Verify and a retry. Then it flows to the new destination.
Rejected: refusal, because to redirect a paused Run a human would first
have to unpause it, and the Run would flow to the OLD destination until
the redirect landed.

What the tests pin, in plain words:
  1. Allowed and held. After a Pause, redirect_run returns True. The Run
     is still paused. The new destination is held in memory and saved for
     a restart. The journal has one redirected row naming the new
     destination and no refusal row. A new tunnel for the Run gets a 503,
     so nothing flows yet.
  2. Control. A redirect on a Run that is not paused still succeeds, and
     it still cuts the live tunnel. Discriminate, do not blur.
  3. Resume lands on the new destination. Verify then retry resumes the
     Run. A new tunnel then reaches the NEW destination, and the old
     destination received only the 5 bytes sent before the cut.
  4. The redirect holds through resume. It is not used up by the first
     resumed tunnel. A second tunnel also lands on the new destination.
  5. The panel. Pause then redirect folds to PAUSED, never REDIRECTED,
     because the Run is held and a human who read REDIRECTED would think
     it flows. After Verify and retry it folds to FLOWING. One variant
     uses hand-built rows, one uses the real rows of the real sequence.

Expected today: every test passes. A scratch run of the whole sequence
showed that the proxy already does all of this. If so, there is no
production fix for this file. This file is then a regression guard for the
allowed rule, and it does not need a fix to go green.

What the held attempt journals, read from a real run and pinned by
filtering, not by row totals: a tunnel request for a paused Run adds one
row, outcome paused, reason "human pause", http_status 503, naming the NEW
destination, because the redirect is already held. It adds no redirected
row and no blocked row.

What check 4 found in the code: retry_run forgets the pause and keeps the
redirect ("a redirect stays on its human-chosen destination", and its
docstring says so). do_CONNECT consults the held redirect on every
tunnel. So the redirect is not consumed. This is pinned as the code
behaves, and it reads as the right rule: a Run that was moved stays moved.

A question for Charles, NOT pinned here: the panel folds the Verify row
(outcome empty, reason "verify ok") as a resume event. So between Verify
and retry the panel shows FLOWING while the proxy still holds the Run and
answers 503. The states.py docstring says VERIFY_OK means "checked clean
and waiting on a human to let it go again". The Verify path never reaches
it. A later test can pin the intended fold once Charles decides.

The harness is the usage test's. The one change is a counting echo, so the
test can read how many bytes each destination really received, apart from
what the journal claims. The cut tunnel's close row lands on the handler's
thread, so the tests wait for rows with a deadline and fail loudly.
wait_for_close_row keys on bytes and returns on the first row that carries
5 bytes, which is the cut tunnel's close row. A row for the resumed tunnel
is found by its destination, never by first match.

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
# The tunnel carries this payload each way before anything is steered.
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


class PauseThenRedirectTest(unittest.TestCase):
    """Steering a Run that is already paused."""

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

    def wait_for_close_row(self, trace):
        """Wait for the cut tunnel's close row: the first row with 5 bytes."""
        return self.wait_for_row(
            trace, lambda r: r["bytes_tx"] == len(PAYLOAD),
            "the cut tunnel's close row")

    def pause_a_live_run(self, trace):
        """Open a tunnel, carry bytes, press Pause, wait for the journal.

        Returns the client socket, so the caller closes it. After this
        returns the journal is settled: the pause row and the cut
        tunnel's close row are both written.
        """
        sock = self.open_and_exchange(trace)
        self.assertTrue(
            self.proxy.state.pause_run(trace), "pause_run refused the Run")
        self.wait_for_close_row(trace)
        return sock

    def verify_then_retry(self, trace):
        """A human Verify, then a retry. Both must succeed."""
        self.assertTrue(
            self.proxy.state.verify_run(trace, DONE_CLAUSE),
            "harness: a human Verify on a paused Run was refused")
        self.assertEqual(
            self.proxy.state.retry_run(trace),
            (True, "retry allowed after verify"),
            "harness: retry after a fresh Verify was refused")

    def resumed_tunnel(self, trace):
        """Carry PAYLOAD through a fresh tunnel, close it, return its row.

        The row is found by its destination and its bytes, never by first
        match: the new tunnel's row is the one that makes the count of
        close rows for the NEW destination go up. Polls with a deadline.
        """
        seen = len(self.close_rows(trace, self.safe_dest))
        sock = self.open_and_exchange(trace)
        sock.close()
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            rows = self.close_rows(trace, self.safe_dest)
            if len(rows) > seen:
                return rows[-1]
            time.sleep(0.02)
        self.fail("the journal never got the resumed tunnel's close row")

    @staticmethod
    def is_close_row(row, dest):
        """A tunnel that finished by itself, to this destination, 5/5 bytes."""
        return (row["outcome"] == "allowed" and row["dest_host"] == dest
                and row["bytes_tx"] == len(PAYLOAD)
                and row["bytes_rx"] == len(PAYLOAD))

    def close_rows(self, trace, dest):
        return [r for r in self.rows_for(trace) if self.is_close_row(r, dest)]

    def received(self):
        """Bytes each destination really received, from the servers."""
        with self.old_dest_server.count_lock:
            old = self.old_dest_server.bytes_in
        with self.new_dest_server.count_lock:
            new = self.new_dest_server.bytes_in
        return old, new

    # ---- check 1 -------------------------------------------------------

    def test_redirect_on_a_paused_run_is_allowed_and_held(self):
        """Check 1: allowed, still paused, held and saved, 503 on a new tunnel."""
        trace = "bench-pause-then-redirect-1"
        sock = self.pause_a_live_run(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest),
                "redirect_run refused a paused Run")
        finally:
            sock.close()
        self.assertTrue(
            self.proxy.state.is_paused(trace),
            "the redirect un-paused the Run")
        self.assertEqual(
            self.proxy.state._redirects[trace]["dest_host"], self.safe_dest,
            "the redirect is not held for the Run")
        self.assertEqual(
            self.saved_redirects()[trace]["dest_host"], self.safe_dest,
            "the redirect is not saved for a restart")
        rows = self.rows_for(trace)
        self.assertEqual(
            [r["dest_host"] for r in rows
             if r["outcome"] == "redirected"
             and r["reason"] == "human redirect"],
            [self.safe_dest],
            "the journal must hold one redirect row naming the new "
            "destination")
        self.assertEqual(
            [r["reason"] for r in rows if r["outcome"] == "blocked"], [],
            "the journal holds a refusal row for an allowed redirect")
        # The Run is held: a new tunnel gets a 503 and nothing flows.
        held_sock, held_status = self.open_tunnel(trace)
        held_sock.close()
        self.assertEqual(
            held_status, 503,
            "a paused and redirected Run must hold new tunnels")
        rows = self.rows_for(trace)
        held_rows = [r for r in rows if r["http_status"] == 503]
        self.assertEqual(
            [(r["outcome"], r["reason"], r["dest_host"]) for r in held_rows],
            [("paused", "human pause", self.safe_dest)],
            "the held attempt must journal as paused, naming the new "
            "destination")
        self.assertEqual(
            [r["outcome"] for r in rows
             if r["outcome"] in ("redirected", "blocked")],
            ["redirected"],
            "the held attempt must add no redirected or blocked row")

    # ---- check 2 -------------------------------------------------------

    def test_redirect_on_a_live_run_still_succeeds(self):
        """Check 2, the control: a Run that is not paused still redirects."""
        trace = "bench-pause-then-redirect-control-1"
        sock = self.open_and_exchange(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest),
                "redirect_run refused a Run that is not paused")
            self.wait_for_close_row(trace)
            self.assert_tunnel_dead(sock)
        finally:
            sock.close()
        self.assertFalse(self.proxy.state.is_paused(trace))
        self.assertEqual(
            self.proxy.state._redirects[trace]["dest_host"], self.safe_dest,
            "the redirect is not held for the Run")
        control = [r for r in self.rows_for(trace)
                   if r["outcome"] == "redirected"
                   and r["reason"] == "human redirect"]
        self.assertEqual(
            [r["dest_host"] for r in control], [self.safe_dest],
            "the journal must hold one redirect row naming the new "
            "destination")

    # ---- check 3 -------------------------------------------------------

    def test_resume_lands_on_the_new_destination(self):
        """Check 3: Verify then retry, then the tunnel reaches the new place."""
        trace = "bench-pause-then-redirect-resume-1"
        sock = self.pause_a_live_run(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest))
            self.verify_then_retry(trace)
        finally:
            sock.close()
        self.assertFalse(
            self.proxy.state.is_paused(trace),
            "the Run is still paused after Verify and retry")
        resumed = self.resumed_tunnel(trace)
        self.assertEqual(
            (resumed["bytes_tx"], resumed["bytes_rx"]),
            (len(PAYLOAD), len(PAYLOAD)))
        # The old destination's only row with bytes is the cut tunnel's
        # close row: exactly the 5 bytes sent before the cut, each way.
        old_rows = [r for r in self.rows_for(trace)
                    if r["dest_host"] == self.dest and r["bytes_tx"] > 0]
        self.assertEqual(
            [(r["bytes_tx"], r["bytes_rx"]) for r in old_rows],
            [(len(PAYLOAD), len(PAYLOAD))],
            "something more than the pre-cut bytes reached the old "
            "destination")
        # And the servers agree, apart from what the journal claims.
        old_bytes, new_bytes = self.received()
        self.assertEqual(
            old_bytes, len(PAYLOAD),
            "the old destination received bytes after the cut")
        self.assertEqual(
            new_bytes, len(PAYLOAD),
            "the resumed tunnel did not reach the new destination")

    # ---- check 4 -------------------------------------------------------

    def test_redirect_holds_through_resume(self):
        """Check 4: the redirect is not used up by the first resumed tunnel."""
        trace = "bench-pause-then-redirect-holds-1"
        sock = self.pause_a_live_run(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest))
            self.verify_then_retry(trace)
        finally:
            sock.close()
        first = self.resumed_tunnel(trace)
        second = self.resumed_tunnel(trace)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(
            self.proxy.state._redirects[trace]["dest_host"], self.safe_dest,
            "the redirect was used up by the first resumed tunnel")
        self.assertEqual(
            self.saved_redirects()[trace]["dest_host"], self.safe_dest,
            "the saved redirect was cleared by the resume")
        old_bytes, new_bytes = self.received()
        self.assertEqual(old_bytes, len(PAYLOAD))
        self.assertEqual(
            new_bytes, 2 * len(PAYLOAD),
            "the second resumed tunnel did not reach the new destination")

    # ---- check 5 -------------------------------------------------------

    def test_panel_folds_pause_then_redirect_to_paused(self):
        """Check 5, hand-built rows in the shapes the proxy writes."""
        pause = journal_row("paused", "human pause")
        cut_close = journal_row("paused", "tunnel cut by pause")
        redirect = journal_row("redirected", "human redirect")
        held_attempt = journal_row("paused", "human pause")
        verify = journal_row("", "verify ok")
        resume = journal_row("", "retry allowed after verify")
        self.assertEqual(
            states_module.run_state([pause, redirect]), "PAUSED",
            "a Run that is paused and then redirected is held, so the "
            "panel must say PAUSED, not REDIRECTED")
        self.assertEqual(
            states_module.run_state(
                [pause, cut_close, redirect, held_attempt]), "PAUSED",
            "a held attempt must not change the run state")
        self.assertEqual(
            states_module.run_state(
                [pause, cut_close, redirect, held_attempt, verify, resume]),
            "FLOWING",
            "after Verify and retry the Run flows")

    def test_panel_folds_the_real_rows_of_the_allowed_sequence(self):
        """Check 5, the same fold over rows the real proxy wrote."""
        trace = "bench-pause-then-redirect-panel-1"
        sock = self.pause_a_live_run(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest))
            held_sock, held_status = self.open_tunnel(trace)
            held_sock.close()
            self.assertEqual(held_status, 503, "harness: the Run was not held")
            self.verify_then_retry(trace)
        finally:
            sock.close()
        rows = self.rows_for(trace)
        redirect_at = [i for i, r in enumerate(rows)
                       if r["outcome"] == "redirected"
                       and r["reason"] == "human redirect"][0]
        held_at = [i for i, r in enumerate(rows)
                   if r["http_status"] == 503][0]
        self.assertEqual(
            states_module.run_state(rows[:redirect_at + 1]), "PAUSED",
            "the real rows of pause then redirect must fold to PAUSED")
        self.assertEqual(
            states_module.run_state(rows[:held_at + 1]), "PAUSED",
            "a real held attempt must not change the run state")
        self.assertEqual(
            states_module.run_state(rows), "FLOWING",
            "the real rows after Verify and retry must fold to FLOWING")


if __name__ == "__main__":
    unittest.main()
