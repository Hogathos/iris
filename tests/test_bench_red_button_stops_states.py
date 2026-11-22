"""Cross-test 3: the Red button stops a live, a paused, and a redirected Run.

Test only. Charles reviews it first. A fix prompt follows only if a check
fails. Nothing is fixed here.

This is the third file in the cross-test queue, after pause-then-redirect
(PR #9) and redirect-then-pause (PR #11). The rule it pins is ALLOW,
decided by Charles 2026-11-21 02:19 EST: steering a steered Run is
allowed. The Red button must stop all three tunnel states at once, and
none of them must ever restart on its own.

What the tests pin, in plain words:
  1. The button stops a live Run. The tunnel is cut. The journal holds a
     stop row. A new tunnel gets a 503. Nothing more reaches upstream.
     (Passes today.)
  2. The button stops a paused Run. The Run has a stop row. It is still
     held. approve_run refuses it. retry_run is blocked with reason
     "red button stopped". The pause-cut tunnel's close row reads paused
     with its 5 bytes. (Expected to FAIL today: the retry reason reads
     "verify required before retry", because retry_run sees the Run as
     paused first and never reaches the stopped branch.)
  3. The button stops a redirected Run. The tunnel to the NEW destination
     is cut. The Run has a stop row. It is still held. approve_run
     refuses it. retry_run is blocked with reason "red button stopped".
     The redirect entry survives the press, in memory and saved.
     (Expected to FAIL today, same retry reason as check 2. The surviving
     redirect is a question for Charles, not a failure: should a dead Run
     keep its redirect?)
  4. A stopped Run never restarts through Verify and retry. A live Run
     cannot come back: Verify refuses it and retry stays blocked. A
     paused or redirected Run can come back today, because Verify still
     sees it as paused or redirected and retry lets it through. (Expected
     to FAIL today for the paused and redirected Runs.)
  5. A tunnel the button cut is not a completed attempt. No cut tunnel's
     close row reads "allowed". The close row names what cut it:
     "human red button" for the button, the same words as the stop row,
     and "tunnel cut by pause" for Pause. (Expected to FAIL today for
     the live and redirected tunnels: both read "allowed" with status
     "success". The paused tunnel reads "paused", because Pause marked
     its cut. The likely fix is _mark_cut in the red button cut loop,
     in a separate prompt.)
  6. The panel. [live, stopped] folds to STOPPED; [paused, stopped]
     folds to STOPPED; [redirected, stopped] folds to STOPPED. A held
     attempt after the press changes none of them. One variant uses
     hand-built rows, one uses the real rows of the real sequence.
     (Passes today.)

Why check 5 fails: the button cuts the socket without _mark_cut, and
the close path only reads steer_cut when the relay did not end clean.
A cut tunnel ends clean, with status "success", so the close row reads
"allowed". The stop row is there, but the close row tells a different
story. That is the gap.

Not pinned here: the set_override cut loop, which also lacks _mark_cut.
That belongs to the spend cap + per-network override item.

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

# One Run per state, all live at once, one press.
LIVE = "bench-red-button-live"
PAUSED = "bench-red-button-paused"
REDIRECTED = "bench-red-button-redirected"


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


class RedButtonStopsStatesTest(unittest.TestCase):
    """One press stops a live, a paused, and a redirected Run."""

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

    def pause_a_live_run(self, trace):
        """Pause a Run with a live tunnel. The tunnel is cut and closed."""
        sock = self.open_and_exchange(trace)
        try:
            self.assertTrue(
                self.proxy.state.pause_run(trace),
                "harness: pause_run refused a live Run")
            self.wait_for_row(
                trace,
                lambda r: r["reason"] == "tunnel cut by pause",
                "the pause-cut tunnel's close row")
            self.assert_tunnel_dead(sock)
        finally:
            sock.close()

    def redirect_a_live_run(self, trace, baseline=(0, 0)):
        """Redirect a Run with a live tunnel, then flow to the new place.

        Opens a tunnel to the old destination, redirects the Run (which
        cuts that tunnel), waits for the cut tunnel's close row, then opens
        a second tunnel that must flow to the NEW destination. Returns the
        second socket, so the caller closes it. baseline is the bytes the
        destinations had already received, so the check reads the delta.
        """
        first = self.open_and_exchange(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest),
                "harness: redirect_run refused a live Run")
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
            (old_bytes, new_bytes),
            (baseline[0] + len(PAYLOAD), baseline[1] + len(PAYLOAD)),
            "harness: the second tunnel did not flow to the new destination")
        return second

    def set_up_three_runs(self):
        """One Run per state, all live at once. Returns (live, redirected).

        The live Run keeps its tunnel. The paused Run's tunnel is already
        cut by Pause. The redirected Run's second tunnel flows to the NEW
        destination. Each Run has carried PAYLOAD each way.
        """
        live = self.open_and_exchange(LIVE)
        self.pause_a_live_run(PAUSED)
        baseline = self.received()
        redirected = self.redirect_a_live_run(REDIRECTED, baseline)
        return live, redirected

    def press(self):
        """Press the Red button once. It must stop every Run."""
        self.assertTrue(
            self.proxy.state.red_button(),
            "the Red button refused to stop the Runs")

    def stop_rows(self, trace):
        """The stop rows for one Run: outcome stopped, human red button.

        A cut tunnel's close row now reads stopped too, with the same
        reason, so the stop row is the stopped row that carries no
        bytes: control rows move no traffic, close rows keep theirs.
        """
        return [r for r in self.rows_for(trace)
                if r["outcome"] == "stopped"
                and r["reason"] == "human red button"
                and r["bytes_tx"] == 0 and r["bytes_rx"] == 0]

    def cut_close_row(self, trace, dest):
        """The close row of the tunnel the button cut for one Run.

        Found by destination and bytes, never by first match: the
        redirected Run has two close rows with 5 bytes, one per tunnel.
        """
        rows = self.wait_for_row(
            trace,
            lambda r: r["method"] == "CONNECT"
            and r["dest_host"] == dest
            and r["bytes_tx"] == len(PAYLOAD),
            "the cut tunnel's close row")
        return rows[-1]

    # ---- check 1 -------------------------------------------------------

    def test_red_button_stops_a_live_run(self):
        """Check 1: the button cuts the live tunnel and holds the Run."""
        live, redirected = self.set_up_three_runs()
        old_before, new_before = self.received()
        self.press()
        try:
            self.assert_tunnel_dead(live)
        finally:
            live.close()
            redirected.close()
        stop = self.stop_rows(LIVE)
        self.assertEqual(len(stop), 1, "the live Run has no stop row")
        self.assertEqual(
            stop[0]["status"], "unknown",
            "the stop row must stay Unverified, never success")
        held_sock, held_status = self.open_tunnel(LIVE)
        held_sock.close()
        self.assertEqual(
            held_status, 503, "a stopped Run must hold new tunnels")
        self.assertEqual(
            self.received(), (old_before, new_before),
            "bytes reached a destination after the press")

    # ---- check 2 -------------------------------------------------------

    def test_red_button_stops_a_paused_run(self):
        """Check 2: a paused Run stays held, and retry stays blocked."""
        live, redirected = self.set_up_three_runs()
        self.press()
        live.close()
        redirected.close()
        stop = self.stop_rows(PAUSED)
        self.assertEqual(len(stop), 1, "the paused Run has no stop row")
        self.assertTrue(
            self.proxy.state.is_paused(PAUSED),
            "the press cleared the pause, so the Run is no longer paused")
        held_sock, held_status = self.open_tunnel(PAUSED)
        held_sock.close()
        self.assertEqual(
            held_status, 503, "a stopped Run must hold new tunnels")
        self.assertFalse(
            self.proxy.state.approve_run(PAUSED, DONE_CLAUSE),
            "approve_run let a stopped Run back in")
        cut = [r for r in self.rows_for(PAUSED)
               if r["reason"] == "tunnel cut by pause"]
        self.assertEqual(
            [(r["outcome"], r["bytes_tx"], r["bytes_rx"]) for r in cut],
            [("paused", len(PAYLOAD), len(PAYLOAD))],
            "the pause-cut tunnel's close row must read paused "
            "and keep its bytes")
        self.assertEqual(
            self.proxy.state.retry_run(PAUSED),
            (False, "red button stopped"),
            "a stopped Run must retry as stopped, not as paused")

    # ---- check 3 -------------------------------------------------------

    def test_red_button_stops_a_redirected_run(self):
        """Check 3: a redirected Run stays held, and its redirect survives.

        The redirect surviving a dead Run is a question for Charles, not
        a failure: the test pins that it survives, and the question is
        whether a dead Run should keep its redirect at all.
        """
        live, redirected = self.set_up_three_runs()
        old_before, new_before = self.received()
        self.press()
        try:
            self.assert_tunnel_dead(redirected)
        finally:
            live.close()
            redirected.close()
        stop = self.stop_rows(REDIRECTED)
        self.assertEqual(len(stop), 1, "the redirected Run has no stop row")
        held_sock, held_status = self.open_tunnel(REDIRECTED)
        held_sock.close()
        self.assertEqual(
            held_status, 503, "a stopped Run must hold new tunnels")
        self.assertFalse(
            self.proxy.state.approve_run(REDIRECTED, DONE_CLAUSE),
            "approve_run let a stopped Run back in")
        self.assertEqual(
            self.received(), (old_before, new_before),
            "bytes reached a destination after the press")
        self.assertEqual(
            self.proxy.state._redirects[REDIRECTED]["dest_host"],
            self.safe_dest,
            "the press dropped the redirect held in memory")
        self.assertEqual(
            self.saved_redirects()[REDIRECTED]["dest_host"], self.safe_dest,
            "the press dropped the redirect saved for a restart")
        self.assertEqual(
            self.proxy.state.retry_run(REDIRECTED),
            (False, "red button stopped"),
            "a stopped Run must retry as stopped, not as redirected")

    # ---- check 4 -------------------------------------------------------

    def test_a_stopped_run_never_restarts_through_verify_and_retry(self):
        """Check 4: Verify plus retry must not bring a stopped Run back."""
        live, redirected = self.set_up_three_runs()
        self.press()
        live.close()
        redirected.close()
        for trace in (LIVE, PAUSED, REDIRECTED):
            with self.subTest(run=trace):
                self.proxy.state.verify_run(trace, DONE_CLAUSE)
                allowed, reason = self.proxy.state.retry_run(trace)
                self.assertFalse(
                    allowed,
                    f"a retry on a stopped Run was allowed ({reason}), "
                    "but an old Run never restarts")
                self.assertEqual(
                    reason, "red button stopped",
                    "a blocked retry on a stopped Run must say so")

    # ---- check 5 -------------------------------------------------------

    def test_a_tunnel_the_button_cut_is_not_a_completed_attempt(self):
        """Check 5: no cut tunnel's close row reads allowed."""
        live, redirected = self.set_up_three_runs()
        self.press()
        live.close()
        redirected.close()
        for trace, dest, label, cut_reason in (
                (LIVE, self.dest, "live", "human red button"),
                (PAUSED, self.dest, "paused", "tunnel cut by pause"),
                (REDIRECTED, self.safe_dest, "redirected",
                 "human red button")):
            with self.subTest(run=label):
                row = self.cut_close_row(trace, dest)
                self.assertNotEqual(
                    row["outcome"], "allowed",
                    "a tunnel the Red button cut is journaled as a "
                    "completed attempt: the cut mark is missing in the "
                    "red_button cut loop")
                self.assertEqual(
                    row["reason"], cut_reason,
                    "the cut tunnel's close row must name what cut it")
                self.assertEqual(
                    (row["bytes_tx"], row["bytes_rx"]),
                    (len(PAYLOAD), len(PAYLOAD)),
                    "the cut tunnel's close row lost its bytes")

    # ---- check 6 -------------------------------------------------------

    def test_panel_folds_every_state_then_stopped_to_stopped(self):
        """Check 6, hand-built rows in the shapes the proxy writes."""
        stop = journal_row("stopped", "human red button")
        held = journal_row("stopped", "approval required after red button")
        cases = (
            ("live", [stop]),
            ("paused", [journal_row("paused", "human pause"),
                        journal_row("paused", "tunnel cut by pause"),
                        stop]),
            ("redirected", [journal_row("redirected", "human redirect"),
                            journal_row("redirected",
                                        "tunnel cut by redirect"),
                            stop]),
        )
        for label, prefix in cases:
            with self.subTest(state=label):
                self.assertEqual(
                    states_module.run_state(prefix), "STOPPED",
                    "a stopped Run must fold to STOPPED")
                self.assertEqual(
                    states_module.run_state(prefix + [held]), "STOPPED",
                    "a held attempt after the press must not change "
                    "the run state")

    def test_panel_folds_the_real_rows_of_every_state(self):
        """Check 6, the same fold over rows the real proxy wrote."""
        live, redirected = self.set_up_three_runs()
        self.press()
        live.close()
        redirected.close()
        for trace in (LIVE, PAUSED, REDIRECTED):
            with self.subTest(run=trace):
                held_sock, held_status = self.open_tunnel(trace)
                held_sock.close()
                self.assertEqual(
                    held_status, 503, "harness: the Run was not held")
                rows = self.rows_for(trace)
                stop_at = [i for i, r in enumerate(rows)
                           if r["outcome"] == "stopped"
                           and r["reason"] == "human red button"][0]
                self.assertEqual(
                    states_module.run_state(rows[:stop_at + 1]), "STOPPED",
                    "the real rows up to the stop row must fold to STOPPED")
                self.assertEqual(
                    states_module.run_state(rows), "STOPPED",
                    "the real rows after the press must fold to STOPPED")


if __name__ == "__main__":
    unittest.main()
