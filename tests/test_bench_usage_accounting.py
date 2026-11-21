"""Bench Part B, usage-accounting test: a cut tunnel is not a completed attempt.

Test-first. Three of the five tests below are expected to FAIL on abfb09b
(the 1d head). Charles reviews this test first. Nothing is fixed until
he does.

Problem in one sentence: a tunnel cut by Pause or Redirect leaves a close
row that says outcome allowed, status success, so every reader that
counts allowed rows or successes counts a stopped attempt as a completed
one, and prices it as one forwarded request.

Why it happens (proxy.py, do_CONNECT, read at abfb09b): the cut shuts the
proxy side of the client socket, so the relay reads a clean EOF and
returns "success". The close path only journals a steer row when
`cut is not None and status != "success"`, so the guard is skipped and the
row is written as a normal allowed row. A normal tunnel that closes by
itself leaves the same row, so no reader can tell the two apart today.

Decisions, dated 2026-11-19, made by Charles. They are locked. They are
recorded here because they change what this test pins.
  1. Pin the readers' outputs, not the row's wording. This test asserts
     what the readers conclude, never the outcome or close_reason
     strings on the row. Rejected: pinning the row fields, because that
     locks in one fix.
  2. Patch a test-only price for 127.0.0.1 into the price table. The
     real table holds loopback at $0.00, so a cut tunnel prices to $0.00
     today by luck and a cost check would prove nothing. With a price in
     place, a cut tunnel that is still counted as allowed prices to one
     forwarded request, and the check can fail. Rejected: loopback cost
     only, because it passes today.
  3. Neighbour included: a tunnel cut by Redirect, the redirected
     double-count. One attempt cut by redirect_run must not add to
     allowed.
  4. Fix A: the close path journals a cut tunnel as paused, redirected or
     stopped with status unknown and keeps the bytes. The readers are not
     changed. Rejected: fix B (keep allowed, add a marker, teach every
     reader), because it touches six readers and the next reader built
     forgets the marker.

The readers this test goes through, all real, none mirrored:
  - db.fetch_outcome_counts: the allowed count (report.py and /stats)
  - db.fetch_totals: the successes count (report.py success rate)
  - api.stats(): the numbers the dashboard fetches, including total cost
  - api.summarize_costs and api.attempt_cost: priced cost, row by row
  - ProxyState.capped_total: the money that feeds the spend cap
  - devpanel panel._row_words and states.run_state: the plain words and
    the run state the panel shows

What the assertions mean in plain words:
  - A cut tunnel is not counted as allowed, and not counted as a success.
  - A cut tunnel costs $0.00 on every reader, even when its destination
    has a price. The money spent and the spend cap never include it.
  - The panel never says "allowed: run flowed." about a cut tunnel.
  - A tunnel that finishes by itself still counts as one allowed, one
    success, and one priced request. The test discriminates, it does not
    make every allowed row look cut.

What is NOT pinned here, on purpose:
  - The dashboard's word for a row ("allowed", "paused") is JavaScript
    inside api.py. No test here runs it. This test pins the dashboard's
    numbers (api.stats), not its words. Next check: run that function in
    a JS runner, or move the word choice into Python.
  - "Counts as redirected once" in the redirect test. The redirect_run
    control row is itself a redirected row, so the redirected count is
    2 under fix A and cannot be exactly 1 for any fix that journals the
    cut tunnel as redirected. This test pins the part that fails today:
    the cut tunnel adds nothing to allowed or to successes.
  - Which platform journals the cut as allowed. Linux does, from the
    EOF path. Windows may journal it as paused from the error path. The
    readers decide here, so the test passes the same way on both once
    the fix is in. Unverified until the platform matrix runs.
  - Usage. The close row keeps the bytes, and no reader totals usage
    apart from cost yet. $0.00 is billable cost, not total usage.

How the close row is awaited, no luck: the cut tunnel's close row lands
on the handler's own thread after pause_run or redirect_run returns. The
tunnel carries 5 bytes each way before the cut and the control rows carry
none, so the close row is the Run's row with 5 bytes sent. The test polls
for it with a deadline and fails loudly if it never appears.

Why the dead check comes second: the cut can reach the relay in two ways.
Mostly the relay reads a clean EOF and the row says allowed (the bug). Now
and then the relay's recv raises instead, and the close path already
journals a steer row (outcome paused or redirected, status unknown,
close_reason client_eof). Sending a byte into the cut socket provokes the
second way: the proxy answers with a reset. Measured at abfb09b, a Redirect
cut with a ping sent straight after journaled the steer row once in 30
runs. So the test waits for the close row first, then checks the tunnel
is dead. The dead check still proves the tunnel carries nothing. It can
no longer change what the handler journals. The same split shows the
journal wording of a cut depends on timing, not only on the platform.

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
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import api as api_module
import db as db_module
import proxy as proxy_module
from devpanel import panel as panel_module
from devpanel import states as states_module

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
# What the panel says about a row that flowed. A cut tunnel must never
# get these words.
FLOWED_WORDS = "allowed: run flowed."


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class UsageAccountingTest(unittest.TestCase):
    """A cut tunnel is not a completed attempt, in every reader."""

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
        # High rate limit: rate limiting has its own tests, and a 429
        # here would mask the tunnel this test needs to open.
        self.proxy = proxy_module.serve(
            "127.0.0.1", 0, self.db_path, rate_limit=10000)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.proxy_port = self.proxy.server_address[1]
        self.dest = f"127.0.0.1:{self.echo.server_address[1]}"
        self.safe_dest = f"127.0.0.1:{self.safe.server_address[1]}"

    def tearDown(self):
        self.proxy.shutdown()
        self.echo.shutdown()
        self.safe.shutdown()
        self.proxy.server_close()
        self.echo.server_close()
        self.safe.server_close()
        self.proxy.state.conn.close()
        self.tmp.cleanup()

    # ---- harness -------------------------------------------------------

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

        Same rule as the Pause race fix, runs 67 to 71. A graceful
        hangup reads b"" (EOF), an abrupt one raises OSError, and on
        Linux the first send into a just-shut socket can still succeed,
        so the receive side decides. A timeout means still alive.
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

    def read_everything(self, trace):
        """Run every reader on the journal, with a test-only price in place.

        Returns a plain dict so the assertions below read in plain words.
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
            rows = self.rows_for(trace)
            summary = api_module.summarize_costs(rows)
            return {
                "rows": rows,
                "allowed": counts["allowed"],
                "successes": totals["successes"] or 0,
                "dashboard": api_module.stats(),
                "row_costs": [api_module.attempt_cost(r) for r in rows],
                "run_cost": summary["cost"],
                "capped": self.proxy.state.capped_total(self.dest),
                "words": [panel_module._row_words(r) for r in rows],
                "run_state": states_module.run_state(rows),
            }

    def assert_counted_as_cut(self, seen, run_state):
        """The readers all treat the Run's tunnel as cut, not completed."""
        self.assertEqual(
            seen["allowed"], 0,
            "a cut tunnel is counted as an allowed attempt "
            "(db.fetch_outcome_counts)")
        self.assertEqual(
            seen["successes"], 0,
            "a cut tunnel is counted as a success (db.fetch_totals)")
        self.assertEqual(
            seen["dashboard"]["allowed"], 0,
            "the dashboard numbers count a cut tunnel as allowed")
        self.assertEqual(
            seen["dashboard"]["successes"], 0,
            "the dashboard numbers count a cut tunnel as a success")
        self.assertEqual(
            seen["row_costs"], [0.0] * len(seen["rows"]),
            "a cut tunnel is priced as a forwarded request, so some row "
            "does not cost $0.00 (api.attempt_cost)")
        self.assertEqual(
            seen["run_cost"], 0.0,
            "the Run's money spent is not $0.00 (api.summarize_costs)")
        self.assertEqual(
            seen["dashboard"]["total_cost"], 0.0,
            "the dashboard total cost is not $0.00 (api.stats)")
        self.assertEqual(
            seen["capped"], 0.0,
            "the spend cap total includes a cut tunnel "
            "(ProxyState.capped_total)")
        self.assertNotIn(
            FLOWED_WORDS, seen["words"],
            "the panel says a cut tunnel flowed (devpanel _row_words)")
        self.assertEqual(
            seen["run_state"], run_state,
            "the panel shows the wrong run state for a cut Run")

    # ---- the tests -----------------------------------------------------

    def test_plain_cut_is_not_a_completed_attempt(self):
        """Check 1: Pause cuts a live tunnel."""
        trace = "bench-usage-plain-cut-1"
        sock = self.open_and_exchange(trace)
        try:
            self.assertTrue(
                self.proxy.state.pause_run(trace), "pause_run refused the Run")
            # pause_run has returned, so the cut has landed. Wait for the
            # close row BEFORE touching the cut socket: see "Why the dead
            # check comes second" in the module docstring.
            self.wait_for_close_row(trace)
            self.assert_tunnel_dead(sock)
        finally:
            sock.close()
        self.assert_counted_as_cut(self.read_everything(trace), "PAUSED")

    def test_swept_tunnel_cut_is_not_a_completed_attempt(self):
        """Check 2: a late tunnel cut by the second sweep (the 1d harness)."""
        trace = "bench-usage-swept-cut-1"
        in_journal = threading.Event()   # pause_run is past the pop
        release = threading.Event()      # test lets pause_run finish
        real_save_paused = proxy_module.db_module.save_paused

        def held_save_paused(conn, trace_id, *args, **kwargs):
            # Hold only this Run's pause, and only the first call: the
            # first thing pause_run does after the pop and before the flag.
            if trace_id == trace and not in_journal.is_set():
                in_journal.set()
                if not release.wait(timeout=HOLD_TIMEOUT):
                    raise OSError("test harness: journal hold never released")
            return real_save_paused(conn, trace_id, *args, **kwargs)

        pause_result = {}

        def run_pause():
            try:
                pause_result["ok"] = self.proxy.state.pause_run(trace)
            except Exception as exc:  # reported by the assertions below
                pause_result["error"] = repr(exc)

        sock = None
        with mock.patch.object(
                proxy_module.db_module, "save_paused", held_save_paused):
            pauser = threading.Thread(target=run_pause, daemon=True)
            pauser.start()
            try:
                self.assertTrue(
                    in_journal.wait(timeout=STEP_TIMEOUT),
                    "harness: pause_run never reached the journal step, "
                    "so the pop-to-flag gap was never entered")
                # pause_run has popped the live tunnels and has not set
                # its flag. The tunnel registers in the gap and carries
                # its bytes before the sweep cuts it.
                sock = self.open_and_exchange(trace)
            finally:
                release.set()
            pauser.join(timeout=HOLD_TIMEOUT + STEP_TIMEOUT)
        try:
            self.assertFalse(pauser.is_alive(), "harness: pause_run hung")
            self.assertNotIn("error", pause_result, pause_result.get("error"))
            self.assertTrue(pause_result.get("ok"), "pause_run refused the Run")
            # Close row first, then the dead check: see the module docstring.
            self.wait_for_close_row(trace)
            self.assert_tunnel_dead(sock)
        finally:
            if sock is not None:
                sock.close()
        self.assert_counted_as_cut(self.read_everything(trace), "PAUSED")

    def test_tunnel_that_completes_still_counts_as_completed(self):
        """Check 3, the control: no Pause, the client closes the tunnel."""
        trace = "bench-usage-control-1"
        sock = self.open_and_exchange(trace)
        sock.close()
        self.wait_for_close_row(trace)
        seen = self.read_everything(trace)
        self.assertEqual(seen["allowed"], 1, "a finished tunnel must count")
        self.assertEqual(seen["successes"], 1, "a finished tunnel is a success")
        self.assertEqual(seen["dashboard"]["allowed"], 1)
        self.assertEqual(seen["dashboard"]["successes"], 1)
        self.assertEqual(
            seen["row_costs"], [TEST_PRICE],
            "a finished tunnel prices as one forwarded request")
        self.assertEqual(seen["run_cost"], TEST_PRICE)
        self.assertEqual(seen["capped"], TEST_PRICE)
        self.assertEqual(seen["words"], [FLOWED_WORDS])
        self.assertEqual(seen["run_state"], "FLOWING")

    def test_redirect_cut_is_not_a_completed_attempt(self):
        """Check 4: redirect_run on a live tunnel, the double-count."""
        trace = "bench-usage-redirect-cut-1"
        sock = self.open_and_exchange(trace)
        try:
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest),
                "redirect_run refused the Run")
            # redirect_run has returned, so the cut has landed. Close row
            # first, then the dead check: see the module docstring.
            self.wait_for_close_row(trace)
            self.assert_tunnel_dead(sock)
        finally:
            sock.close()
        seen = self.read_everything(trace)
        # One attempt, two rows today: the redirect row and an allowed
        # close row. The attempt must not also be counted as allowed.
        self.assert_counted_as_cut(seen, "REDIRECTED")

    def test_unrelated_run_on_a_redirected_proxy_still_counts(self):
        """Guard against the obvious wrong fix, a steer test on the Run.

        steer_cut looks at the Run's state, not at the tunnel. If the fix
        journals every tunnel that ends while its Run is redirected as
        redirected, a tunnel that opens AFTER the Redirect and finishes by
        itself would be mislabeled. That tunnel goes to the new
        destination and completes. It must still count as allowed.
        """
        trace = "bench-usage-after-redirect-1"
        self.assertTrue(
            self.proxy.state.redirect_run(trace, self.safe_dest),
            "redirect_run refused the Run")
        sock = self.open_and_exchange(trace)   # asks for A, goes to safe
        sock.close()
        self.wait_for_close_row(trace)
        seen = self.read_everything(trace)
        self.assertEqual(
            seen["allowed"], 1,
            "a tunnel opened after the Redirect and finished by itself "
            "must still count as allowed")
        self.assertEqual(seen["successes"], 1)


if __name__ == "__main__":
    unittest.main()
