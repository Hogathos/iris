"""Bench: spend cap + per-network override, cut close rows (test-first).

Problem in one sentence: a tunnel cut by an override or a cap
block must journal as cut, not as a completed attempt.

Plain words, for evaluators: when Iris stops a tunnel, the journal
must say it stopped it, not that it finished fine.

What the code does today (read 2026-11-21, main d51b6d7d, verify
by running, do not take my word): fix A gave pause_run and
redirect_run a per-tunnel cut mark (_mark_cut), so their close
rows read paused / redirected. set_override and _cap_block cut
live tunnels WITHOUT marking them. Their close rows fall through
to the steer_cut branch, which only fires when status is not
"success". But a shutdown socket ends the relay cleanly: _relay
returns "success" on EOF, so status stays "success", the branch
is skipped, and the close row reads allowed. The existing tests
pin the pause row, the block row, the 503, and the retry gate, but
never read the cut tunnel's close row. This file pins it.

Checks 1 and 2 failed before the fix: the close row read allowed
with status success instead of paused with status unknown. Worse,
the row was nondeterministic: allowed / success when the relay saw
a clean EOF, paused / unknown when a send after the cut broke the
relay (close_reason client_eof). Same action, two different journal
rows, depending on timing. The fix marks the cut where it happens,
the same as pause_run does, and makes the row deterministic. The
close row says "tunnel cut by pause", the cut kind, and the specific
reason (override, cap block) stays on the pause control row
journaled first.

The test never sends on a cut tunnel: a post-cut ping is what
flips the relay from a clean EOF to a broken one, so these checks
prove the cut with a fresh 503 instead of a ping. That keeps the
failure deterministic: allowed, every run.
Checks 3, 4, and 5 pin behavior that already works: warn once,
cap plus override together, and real flow after unblock.

Done clause for this test (human-approved, once per task type, as
a named read-back, before the Run starts):
  task_type = "local file write"
  clause    = "row exists after close/fsync"
  read_back = "named read-back: file row exists after close/fsync"
No approved done = Unverified, never Correct.

Loopback only. Recorded provider costs are simulated by journaling
rows with cost_dollars, the documented nonzero-cost-wins path; the
enforcement under test is real. No bodies, no keys, no Authorization
headers, no real hostnames. No guessed costs, ever.
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
from devpanel import states as panel_states


# Human-approved "done" clause for this test, once per task type,
# as a named read-back, fixed before the Run starts.
HUMAN_APPROVED_DONE = {
    "task_type": "local file write",
    "clause": "row exists after close/fsync",
    "read_back": "named read-back: file row exists after close/fsync",
    "approved_by": "human",
}


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class SpendCapOverrideTest(unittest.TestCase):
    """Override-cut and cap-block-cut tunnels journal as cut, not allowed."""

    def setUp(self):
        """Fresh proxy, fresh temp DB, three loopback destinations."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        self.echoA = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        self.echoB = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        self.echoC = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        threading.Thread(target=self.echoA.serve_forever, daemon=True).start()
        threading.Thread(target=self.echoB.serve_forever, daemon=True).start()
        threading.Thread(target=self.echoC.serve_forever, daemon=True).start()
        # Rate limiting has its own tests; this proxy serves a high
        # limit so a 429 never masks the 503 cut refusal checked below.
        self.proxy = proxy_module.serve("127.0.0.1", 0, self.db_path, rate_limit=10000)
        self.proxy_port = self.proxy.server_address[1]
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.proxy.state.conn.close)
        self.addCleanup(self.echoA.server_close)
        self.addCleanup(self.echoB.server_close)
        self.addCleanup(self.echoC.server_close)
        self.addCleanup(self.proxy.server_close)
        self.addCleanup(self.echoA.shutdown)
        self.addCleanup(self.echoB.shutdown)
        self.addCleanup(self.echoC.shutdown)
        self.addCleanup(self.proxy.shutdown)
        self.destA = f"127.0.0.1:{self.echoA.server_address[1]}"
        self.destB = f"127.0.0.1:{self.echoB.server_address[1]}"
        self.destC = f"127.0.0.1:{self.echoC.server_address[1]}"
        assert len({self.destA, self.destB, self.destC}) == 3

    def rows_for(self, trace_id):
        """All journal rows for one trace_id, oldest first. Read only."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM attempts WHERE trace_id = ? ORDER BY id", (trace_id,)).fetchall()
        finally:
            conn.close()

    def rows_all(self):
        """Every journal row, oldest first. Read only, closes in finally."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM attempts ORDER BY id").fetchall()
        finally:
            conn.close()

    def open_tunnel(self, dest, trace_id):
        """Open one raw CONNECT tunnel; return (socket, status code)."""
        sock = socket.create_connection(("127.0.0.1", self.proxy_port), timeout=5)
        sock.settimeout(5)
        sock.sendall((f"CONNECT {dest} HTTP/1.1\r\nHost: {dest}\r\nX-Trace-Id: {trace_id}\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            self.assertTrue(chunk, "proxy never answered CONNECT")
            raw += chunk
        head = raw.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
        return sock, int(head.split()[1])

    def echo_roundtrip(self, sock, payload):
        """Send bytes through a tunnel, expect the same bytes back."""
        sock.sendall(payload)
        got = b""
        while len(got) < len(payload):
            chunk = sock.recv(65536)
            if not chunk:
                break
            got += chunk
        return got

    def assert_tunnel_dead(self, sock):
        """A cut tunnel carries no more bytes: EOF or error, never an echo."""
        sock.settimeout(5)
        try:
            sock.sendall(b"ping-after-cut")
        except socket.timeout:
            self.fail("tunnel send timed out: cut did not take effect")
        except OSError:
            return
        try:
            data = sock.recv(65536)
        except socket.timeout:
            self.fail("tunnel recv timed out: cut did not take effect")
        except OSError:
            return
        self.assertEqual(data, b"", "tunnel still flows after cut")

    def record_cost(self, trace_id, dest, dollars):
        """Simulate one provider-reported cost row. The nonzero-cost-wins
        path is documented schema behavior; enforcement is under test."""
        conn = sqlite3.connect(self.db_path)
        try:
            import db as db_module

            db_module.log_attempt(
                conn,
                trace_id=trace_id,
                task_number=1,
                attempt=1,
                status="success",
                dest_host=dest,
                endpoint=dest,
                method="CONNECT",
                http_status=200,
                outcome="allowed",
                cost_dollars=dollars,
            )
        finally:
            conn.close()

    def wait_for_close_row(self, trace_id, deadline=5.0):
        """Wait for the flowed tunnel's close row: the only row with bytes."""
        end = time.time() + deadline
        while time.time() < end:
            for row in self.rows_for(trace_id):
                if row["bytes_in"] == 5 and row["bytes_out"] == 5:
                    return row
            time.sleep(0.05)
        self.fail(f"no close row with 5/5 bytes for {trace_id} after {deadline}s")

    def test_override_cut_close_row(self):
        """Override-cut tunnel journals paused, not allowed."""
        traceA = "bench-override-cut-run-A"
        sockA, statusA = self.open_tunnel(self.destA, traceA)
        self.assertEqual(statusA, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sockA, b"hello"), b"hello")
            self.assertTrue(
                self.proxy.state.set_override(self.destA, "pause", reason="human override"), "set_override refused net A"
            )
            probe, probe_status = self.open_tunnel(self.destA, traceA)
            probe.close()
            self.assertEqual(probe_status, 503, "overridden network stays cut")
            close = self.wait_for_close_row(traceA)
            # Failed before the fix: the close row read allowed /
            # success, the cut leaking as a completed attempt. The
            # fix marks the cut where it happens, the same as
            # pause_run does.
            self.assertEqual(close["outcome"], "paused")
            self.assertEqual(close["status"], "unknown")
            self.assertEqual(close["reason"], "tunnel cut by pause")
        finally:
            sockA.close()

    def test_cap_block_cut_close_row(self):
        """Cap-block-cut tunnel journals paused, not allowed."""
        traceA = "bench-capblock-cut-run-A"
        self.assertTrue(self.proxy.state.set_cap(self.destA, 1.00, reason="human cap"), "set_cap refused net A")
        sockA, statusA = self.open_tunnel(self.destA, traceA)
        self.assertEqual(statusA, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sockA, b"hello"), b"hello")
            for _ in range(5):
                self.record_cost(traceA, self.destA, 0.20)
            probe, probe_status = self.open_tunnel(self.destA, traceA)
            probe.close()
            self.assertEqual(probe_status, 503, "capped network stays cut")
            close = self.wait_for_close_row(traceA)
            # Failed before the fix, same leak as the override cut:
            # allowed / success instead of paused / unknown.
            self.assertEqual(close["outcome"], "paused")
            self.assertEqual(close["status"], "unknown")
            self.assertEqual(close["reason"], "tunnel cut by pause")
        finally:
            sockA.close()

    def test_warn_once(self):
        """The 80% warn fires once per cap, never twice."""
        traceA = "bench-cap-warn-once-run-A"
        self.assertTrue(self.proxy.state.set_cap(self.destA, 1.00, reason="human cap"))
        for _ in range(4):
            self.record_cost(traceA, self.destA, 0.20)
        probe1, status1 = self.open_tunnel(self.destA, traceA)
        probe1.close()
        self.assertEqual(status1, 200, "warn must not stop the run")
        probe2, status2 = self.open_tunnel(self.destA, traceA)
        probe2.close()
        self.assertEqual(status2, 200, "second probe past 80% still flows")
        warns = [r for r in self.rows_for(traceA) if r["outcome"] == "warned"]
        self.assertEqual(len(warns), 1, "exactly one warn row per cap")
        self.assertEqual(warns[0]["reason"], "cap warn 80%")

    def test_cap_and_override_together(self):
        """Cap blocks A, override pauses B, C flows: each fold reads right."""
        traceA = "bench-cap-override-run-A"
        traceB = "bench-cap-override-run-B"
        traceC = "bench-cap-override-run-C"
        sockA, statusA = self.open_tunnel(self.destA, traceA)
        sockB, statusB = self.open_tunnel(self.destB, traceB)
        sockC, statusC = self.open_tunnel(self.destC, traceC)
        self.assertEqual((statusA, statusB, statusC), (200, 200, 200))
        try:
            for sock in (sockA, sockB, sockC):
                self.assertEqual(self.echo_roundtrip(sock, b"hello"), b"hello")
            self.assertTrue(self.proxy.state.set_override(self.destB, "pause", reason="human override"))
            self.assertTrue(self.proxy.state.set_cap(self.destA, 1.00, reason="human cap"))
            for _ in range(5):
                self.record_cost(traceA, self.destA, 0.20)
            probeA, statusA2 = self.open_tunnel(self.destA, traceA)
            probeA.close()
            self.assertEqual(statusA2, 503, "capped network stays cut")
            probeB, statusB2 = self.open_tunnel(self.destB, traceB)
            probeB.close()
            self.assertEqual(statusB2, 503, "overridden network stays cut")
            sockC2, statusC2 = self.open_tunnel(self.destC, traceC)
            try:
                self.assertEqual(statusC2, 200, "untouched network flows")
                self.assertEqual(self.echo_roundtrip(sockC2, b"still"), b"still")
            finally:
                sockC2.close()
            self.assertEqual(panel_states.run_state(self.rows_for(traceA)), "BLOCKED")
            self.assertEqual(panel_states.run_state(self.rows_for(traceB)), "PAUSED")
            self.assertEqual(panel_states.run_state(self.rows_for(traceC)), "FLOWING")
        finally:
            sockA.close()
            sockB.close()
            sockC.close()

    def test_unblock_flows_again(self):
        """After Verify + unblock, a new tunnel really flows, not just retry."""
        traceA = "bench-cap-unblock-flow-run-A"
        done = HUMAN_APPROVED_DONE
        self.assertTrue(self.proxy.state.set_cap(self.destA, 1.00, reason="human cap"))
        sockA, statusA = self.open_tunnel(self.destA, traceA)
        self.assertEqual(statusA, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sockA, b"hello"), b"hello")
            for _ in range(5):
                self.record_cost(traceA, self.destA, 0.20)
            probe, probe_status = self.open_tunnel(self.destA, traceA)
            probe.close()
            self.assertEqual(probe_status, 503, "capped network stays cut")
            proof_path = os.path.join(self.tmp.name, "cap-proof.txt")
            with open(proof_path, "w") as proof:
                proof.write("row exists after close/fsync")
                proof.flush()
                os.fsync(proof.fileno())
            verdict, _ = self.proxy.state.verify_read_back(
                traceA,
                {
                    "kind": "local-file",
                    "path": proof_path,
                    "expected_text": "row exists after close/fsync",
                    "idempotency_key": "cap-unblock-verify-1",
                },
                done_clause=done,
            )
            self.assertEqual(verdict, "Correct")
            self.assertTrue(self.proxy.state.verify_run(traceA, done_clause=done), "fresh Verify must pass")
            self.assertTrue(self.proxy.state.unblock_cap(self.destA, reason="human unblock"), "human unblock must be accepted")
            sockA2, statusA2 = self.open_tunnel(self.destA, traceA)
            try:
                self.assertEqual(statusA2, 200, "unblocked network flows")
                self.assertEqual(self.echo_roundtrip(sockA2, b"again"), b"again")
            finally:
                sockA2.close()
            allowed, _ = self.proxy.state.retry_run(traceA)
            self.assertTrue(allowed, "retry after unblock must be allowed")
        finally:
            sockA.close()


if __name__ == "__main__":
    unittest.main()
