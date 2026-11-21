"""Bench: the Verify fold — VERIFY_OK between Verify and retry.

Test only. Charles reviews it first. A fix prompt follows only if a
check fails. Nothing is fixed here.

Problem in one sentence: the panel folds the Verify row (outcome
empty, reason "verify ok") as a resume event, so between Verify and
retry it shows FLOWING while the proxy still holds the Run and
answers 503.

What the tests pin, in plain words:
  1. Hand-built rows. Pause then Verify folds to VERIFY_OK, never
     FLOWING. After the retry row it folds to FLOWING. Same for the
     pause-then-redirect sequence: VERIFY_OK after Verify, FLOWING
     after retry.
  2. Real rows. The real sequence folds the same way: VERIFY_OK
     after Verify, FLOWING after retry. And the proxy still answers
     503 to a new tunnel between Verify and retry, so the panel and
     the proxy agree the Run is held.
  3. A held attempt after Verify. Its 503 row journals paused, and
     the fold reads PAUSED. That is honest: the Run is held. It is
     the old accidental correction, now documented, not relied on.
  4. The panel can describe the state it will now show: next_step
     for VERIFY_OK says the Run is checked clean and a human can let
     it go again.

Expected failure right now: _event_for maps the verify-ok row to a
resume event, so check 1 folds FLOWING instead of VERIFY_OK and
check 2 folds FLOWING instead of VERIFY_OK. That failure is the
test-first proof.

The fix, when it comes, is one mapping in devpanel/states.py: the
verify-ok row maps to verify_ok, not resume. The retry row keeps
mapping to resume. The transitions already exist: PAUSED plus
verify_ok goes to VERIFY_OK, VERIFY_OK plus resume goes to FLOWING.

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

import proxy as proxy_module
from devpanel import states as states_module

STEP_TIMEOUT = 5
HOLD_TIMEOUT = 10
PAYLOAD = b"hello"
DONE_CLAUSE = {"approved_by": "human", "read_back": {"ok": True}}


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


def journal_row(outcome, reason, status="unknown"):
    """A journal row as the proxy writes it, with the fields the panel reads."""
    return {"outcome": outcome, "reason": reason, "status": status}


class VerifyFoldTest(unittest.TestCase):
    """Between Verify and retry the panel must say VERIFY_OK."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        self.old_dest_server = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _EchoHandler)
        self.new_dest_server = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _EchoHandler)
        threading.Thread(
            target=self.old_dest_server.serve_forever, daemon=True).start()
        threading.Thread(
            target=self.new_dest_server.serve_forever, daemon=True).start()
        self.proxy = proxy_module.serve(
            "127.0.0.1", 0, self.db_path, rate_limit=10000)
        threading.Thread(
            target=self.proxy.serve_forever, daemon=True).start()
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

    def wait_for_row(self, trace, wanted, what):
        """Wait until a journal row matches. Poll with a deadline, fail loud."""
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            rows = [r for r in self.rows_for(trace) if wanted(r)]
            if rows:
                return rows
            time.sleep(0.02)
        self.fail(f"the journal never got {what}")

    # ---- check 1 -------------------------------------------------------

    def test_hand_built_rows_fold_verify_to_verify_ok(self):
        """Check 1: hand-built rows in the shapes the proxy writes."""
        pause = journal_row("paused", "human pause")
        redirect = journal_row("redirected", "human redirect")
        verify = journal_row("", "verify ok")
        resume = journal_row("", "retry allowed after verify")
        held_attempt = journal_row("paused", "human pause")
        self.assertEqual(
            states_module.run_state([pause, verify]), "VERIFY_OK",
            "after Verify and before retry the Run is checked clean "
            "and held, so the panel must say VERIFY_OK, not FLOWING")
        self.assertEqual(
            states_module.run_state([pause, verify, resume]), "FLOWING",
            "after the retry the Run flows")
        self.assertEqual(
            states_module.run_state([pause, redirect, verify]), "VERIFY_OK",
            "pause then redirect then Verify must also read VERIFY_OK")
        self.assertEqual(
            states_module.run_state(
                [pause, redirect, verify, resume]), "FLOWING",
            "after the retry the redirected Run flows")
        self.assertEqual(
            states_module.run_state(
                [pause, redirect, verify, held_attempt]), "PAUSED",
            "a held attempt after Verify reads PAUSED: the Run is held")

    # ---- check 2 -------------------------------------------------------

    def test_real_rows_fold_verify_to_verify_ok(self):
        """Check 2: the real sequence folds the same way, and the proxy agrees."""
        trace = "bench-verify-fold-1"
        sock, status = self.open_tunnel(trace)
        try:
            self.assertEqual(status, 200, "harness: the tunnel did not open")
            sock.sendall(PAYLOAD)
            self.assertEqual(sock.recv(len(PAYLOAD)), PAYLOAD,
                             "harness: the tunnel did not echo")
            self.assertTrue(
                self.proxy.state.pause_run(trace),
                "pause_run refused the Run")
            self.wait_for_row(
                trace, lambda r: r["bytes_tx"] == len(PAYLOAD),
                "the cut tunnel's close row")
            self.assertTrue(
                self.proxy.state.redirect_run(trace, self.safe_dest),
                "redirect_run refused a paused Run")
            self.assertTrue(
                self.proxy.state.verify_run(trace, DONE_CLAUSE),
                "harness: a human Verify on a paused Run was refused")
            # No retry yet. The panel must say VERIFY_OK, and the proxy
            # must still hold the Run.
            self.assertEqual(
                states_module.run_state(self.rows_for(trace)), "VERIFY_OK",
                "the real rows after Verify must fold to VERIFY_OK")
            held_sock, held_status = self.open_tunnel(trace)
            held_sock.close()
            self.assertEqual(
                held_status, 503,
                "the proxy must still hold the Run after Verify")
            # After the retry the Run flows, and the panel agrees.
            self.assertEqual(
                self.proxy.state.retry_run(trace),
                (True, "retry allowed after verify"),
                "harness: retry after a fresh Verify was refused")
            self.assertEqual(
                states_module.run_state(self.rows_for(trace)), "FLOWING",
                "the real rows after Verify and retry must fold to FLOWING")
            flow_sock, flow_status = self.open_tunnel(trace)
            try:
                self.assertEqual(
                    flow_status, 200, "the Run must flow after retry")
            finally:
                flow_sock.close()
        finally:
            sock.close()

    # ---- check 4 -------------------------------------------------------

    def test_next_step_describes_verify_ok(self):
        """Check 4: the panel can describe the state it will now show."""
        self.assertEqual(
            states_module.next_step("VERIFY_OK", "Unverified"),
            "Run checked clean. A human can let it go again.")


if __name__ == "__main__":
    unittest.main()
