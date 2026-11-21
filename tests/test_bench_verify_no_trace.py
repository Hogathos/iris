"""Bench: Verify unverified, no trace (test-first: fails until built).

One rule, from repo AGENTS.md: "No trace: an agent claims it did
X, and Iris finds no proxy row, no bytes, no tool log, no effect,
and no error. Verdict stays Unverified. Reason is No trace. A human
investigates."

Problem in one sentence: a remote claim with nothing behind it
must say Unverified for the right reason, No trace, and must not
stop the Run.

Plain words, for evaluators: Verify means check what the agent
said it did, against what actually happened, by reading it back,
and say Correct, Incorrect, or Unverified, with a journal before
the call, no scores, no LLM-as-judge.

Done clause for this test (human-approved, once per task type, as
a named read-back, before the Run starts):
  task_type = "local file write"
  clause    = "row exists after close/fsync"
  read_back = "named read-back: file row exists after close/fsync"
No approved done = Unverified, never Correct.

What the code does today (read 2026-11-21, main 7f936241, verify
by running, do not take my word): _read_back has no no-trace
path. Any claim kind that is not local-file or sqlite returns
Unverified with why "remote with no local proof", whether the
journal holds traffic for the Run or nothing at all. The existing
test test_verify_read_back_local_file_sqlite pins the verdict
Unverified for a remote claim, but never pins the why. This file
pins the why, and the line between no trace and no local proof.
A second Verify on the same traceless Run must say No trace again:
the first Verify's own rows are not a trace. A Correct Verify is
different: the read happened, so it counts as a trace, and a later
remote claim on that Run says "remote with no local proof", not
"No trace".

The no-trace check must look at rows that existed BEFORE the
verify call: verify_read_back journals its own call row under the
trace first, so counting rows after the call would always find
one. The fix (a separate prompt, after Charles reviews this test)
belongs before that call row is journaled.

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames. No scores, no
percentages, no "probably correct".
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
DONE_FILE = {
    "task_type": "local file write",
    "clause": "row exists after close/fsync",
    "read_back": "named read-back: file row exists after close/fsync",
    "approved_by": "human",
}


class _CountingEchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream that counts its own connections."""

    def handle(self):
        self.server.count += 1
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class VerifyNoTraceTest(unittest.TestCase):
    """A remote claim with no trace says Unverified, reason No trace."""

    @classmethod
    def setUpClass(cls):
        """Start one loopback echo destination and one proxy on temp db."""
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.echo = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _CountingEchoHandler)
        cls.echo.count = 0
        threading.Thread(target=cls.echo.serve_forever, daemon=True).start()
        cls.proxy = proxy_module.serve("127.0.0.1", 0, cls.db_path)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        cls.destA = f"127.0.0.1:{cls.echo.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        """Shut down proxy, echo, and temp db. No files left behind."""
        cls.proxy.shutdown()
        cls.echo.shutdown()
        cls.proxy.server_close()
        cls.echo.server_close()
        cls.proxy.state.conn.close()
        cls.tmp.cleanup()

    def rows_for(self, trace_id):
        """All journal rows for one trace_id, oldest first. Read only."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT * FROM attempts WHERE trace_id = ? ORDER BY id",
                (trace_id,),
            ).fetchall()
        finally:
            conn.close()

    def open_tunnel(self, dest, trace_id):
        """Open one raw CONNECT tunnel; return (socket, status code)."""
        sock = socket.create_connection(("127.0.0.1", self.proxy_port), timeout=5)
        sock.settimeout(5)
        sock.sendall(
            (
                f"CONNECT {dest} HTTP/1.1\r\n"
                f"Host: {dest}\r\n"
                f"X-Trace-Id: {trace_id}\r\n"
                "\r\n"
            ).encode()
        )
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            self.assertTrue(chunk, "proxy never answered CONNECT")
            raw += chunk
        head = raw.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
        return sock, int(head.split()[1])

    def echo_roundtrip(self, sock, payload):
        """Send bytes through a tunnel, read the echo back."""
        sock.sendall(payload)
        got = b""
        while len(got) < len(payload):
            chunk = sock.recv(65536)
            self.assertTrue(chunk, "tunnel closed mid-roundtrip")
            got += chunk
        return got

    def wait_for_rows(self, trace_id, deadline=5.0):
        """Wait until the journal holds a row for the trace, or fail."""
        end = time.time() + deadline
        while time.time() < end:
            rows = self.rows_for(trace_id)
            if rows:
                return rows
            time.sleep(0.05)
        self.fail(f"no journal row for {trace_id} after {deadline}s")

    def check_card(self, card, verdict):
        """Plain-words card shape: claim, read, source, time, verdict, why, next."""
        for key in ("claim", "what_read", "source", "as_of",
                    "verdict", "why", "next"):
            self.assertIn(key, card, f"card missing {key}")
            self.assertTrue(card[key], f"card {key} must not be empty")
        self.assertEqual(card["verdict"], verdict)
        self.assertNotIn("\n", card["why"], "why is one line")
        self.assertNotIn("probably", card["why"].lower())
        self.assertNotIn("%", card["why"])

    def remote_claim(self, key):
        """A remote-effect claim: the agent says it charged a card."""
        return {
            "kind": "remote",
            "system": "stripe",
            "reference": "pi_test_123",
            "idempotency_key": key,
        }

    def test_no_trace_remote_claim(self):
        """A remote claim with no trace says Unverified, reason No trace."""
        trace = "verify-no-trace-run"
        self.assertTrue(
            hasattr(self.proxy.state, "verify_read_back"),
            "Verify read-back does not exist",
        )
        # No tunnel was ever opened for this trace: no proxy row, no
        # bytes, no tool log, no effect, no error. Nothing to read.
        self.assertEqual(self.rows_for(trace), [])
        verdict, card = self.proxy.state.verify_read_back(
            trace, self.remote_claim("verify-no-trace-1"),
            done_clause=DONE_FILE)
        self.assertEqual(verdict, "Unverified")
        self.check_card(card, "Unverified")
        # The rule, AGENTS.md: reason is No trace, human investigates.
        # Fails today: the code says "remote with no local proof".
        self.assertEqual(card["why"], "No trace")
        self.assertEqual(card["next"], "human investigates")
        # Journal: the call row lands before the verdict row, and the
        # verdict row reads verified + unknown. The why lives in the
        # card; the journal row carries the generic "verify Unverified".
        # The call row shares outcome verified + status unknown with
        # the verdict row, so the verdict row is picked out by its
        # reason, not by outcome and status alone.
        rows = self.rows_for(trace)
        call_rows = [r for r in rows if "verify-no-trace-1" in (r["reason"] or "")]
        self.assertTrue(call_rows, "call row must carry the idempotency key")
        verdict_rows = [
            r for r in rows
            if r["outcome"] == "verified" and r["status"] == "unknown"
            and r["reason"] == "verify Unverified"
        ]
        self.assertTrue(verdict_rows, "Unverified verdict must be journaled")
        self.assertLess(call_rows[0]["id"], verdict_rows[0]["id"],
                        "call row first, verdict after")
        for row in verdict_rows:
            row_text = " ".join(str(row[k]) for k in row.keys())
            self.assertNotIn("Authorization", row_text)
            self.assertNotIn("Bearer", row_text)

    def test_second_verify_still_no_trace(self):
        """A second Verify on the same traceless Run still says No trace."""
        trace = "verify-no-trace-second-run"
        self.assertEqual(self.rows_for(trace), [])
        verdict1, card1 = self.proxy.state.verify_read_back(
            trace, self.remote_claim("verify-no-trace-2a"),
            done_clause=DONE_FILE)
        self.assertEqual(verdict1, "Unverified")
        self.assertEqual(card1["why"], "No trace")
        # The first Verify's own rows are not a trace: its call row
        # and verdict row both carry outcome verified. A second
        # Verify must still say No trace, not "remote with no local
        # proof". Fails today: the second why is the remote line.
        verdict2, card2 = self.proxy.state.verify_read_back(
            trace, self.remote_claim("verify-no-trace-2b"),
            done_clause=DONE_FILE)
        self.assertEqual(verdict2, "Unverified")
        self.check_card(card2, "Unverified")
        self.assertEqual(card2["why"], "No trace")
        self.assertEqual(card2["next"], "human investigates")

    def test_correct_verify_is_a_trace(self):
        """A Correct Verify counts as a trace for a later remote claim."""
        trace = "verify-no-trace-correct-run"
        proof_path = os.path.join(self.tmp.name, "trace-proof.txt")
        with open(proof_path, "w") as proof:
            proof.write("row exists after close/fsync")
            proof.flush()
            os.fsync(proof.fileno())
        verdict1, _ = self.proxy.state.verify_read_back(
            trace,
            {"kind": "local-file", "path": proof_path,
             "expected_text": "row exists after close/fsync",
             "idempotency_key": "verify-trace-correct-1"},
            done_clause=DONE_FILE)
        self.assertEqual(verdict1, "Correct")
        # The read happened, so the Run has a trace. A later remote
        # claim has no trace of ITS effect, so the why is the remote
        # line, not No trace. Guards the refined check: a Correct
        # verdict row (outcome verified, status success) counts.
        verdict2, card2 = self.proxy.state.verify_read_back(
            trace, self.remote_claim("verify-no-trace-2c"),
            done_clause=DONE_FILE)
        self.assertEqual(verdict2, "Unverified")
        self.check_card(card2, "Unverified")
        self.assertEqual(card2["why"], "remote with no local proof")

    def test_remote_claim_with_traffic(self):
        """Traffic with no local proof stays 'remote with no local proof'."""
        trace = "verify-remote-with-traffic-run"
        # This Run DID move bytes through the proxy: a tunnel opened,
        # 5 bytes flowed, the echo came back. There is a trace, so the
        # reason is not No trace. The claim is still about a remote
        # effect nothing local can prove, so it stays Unverified.
        sock, status = self.open_tunnel(self.destA, trace)
        try:
            self.assertEqual(status, 200, "tunnel must open")
            self.assertEqual(self.echo_roundtrip(sock, b"hello"), b"hello")
        finally:
            sock.close()
        self.wait_for_rows(trace)
        verdict, card = self.proxy.state.verify_read_back(
            trace, self.remote_claim("verify-remote-traffic-1"),
            done_clause=DONE_FILE)
        self.assertEqual(verdict, "Unverified")
        self.check_card(card, "Unverified")
        self.assertEqual(card["why"], "remote with no local proof")

    def test_no_trace_does_not_stop_run(self):
        """Unverified for no trace never stops the Run."""
        trace = "verify-no-trace-keeps-flowing-run"
        verdict, _card = self.proxy.state.verify_read_back(
            trace, self.remote_claim("verify-no-trace-2"),
            done_clause=DONE_FILE)
        self.assertEqual(verdict, "Unverified")
        # Only Incorrect stops a Run. No pause row, not paused, and a
        # new tunnel still flows: the Run keeps working.
        self.assertEqual(
            [r for r in self.rows_for(trace) if r["outcome"] == "paused"],
            [], "no-trace Unverified must not pause the Run")
        self.assertFalse(self.proxy.state.is_paused(trace),
                         "no-trace Unverified must not pause the Run")
        allowed, reason = self.proxy.state.retry_run(trace)
        self.assertTrue(allowed, "retry must not be blocked by Unverified")
        self.assertEqual(reason, "not paused")
        before = self.echo.count
        sock, status = self.open_tunnel(self.destA, trace)
        try:
            self.assertEqual(status, 200, "tunnel must still open")
            self.assertEqual(self.echo_roundtrip(sock, b"still"), b"still")
        finally:
            sock.close()
        self.assertEqual(self.echo.count - before, 1,
                         "one new echo connection, delta not absolute")

    def test_no_trace_panel_fold(self):
        """No-trace Unverified folds to FLOWING, verdict Unverified."""
        trace = "verify-no-trace-panel-run"
        verdict, _card = self.proxy.state.verify_read_back(
            trace, self.remote_claim("verify-no-trace-3"),
            done_clause=DONE_FILE)
        self.assertEqual(verdict, "Unverified")
        rows = self.rows_for(trace)
        # A verify row with status unknown moves nothing: the Run
        # keeps its state. The verdict read says Unverified, never
        # Correct: a claim without a read back was never checked.
        self.assertEqual(panel_states.run_state(rows), "FLOWING")
        self.assertEqual(panel_states.verdict_for(rows), "Unverified")


if __name__ == "__main__":
    unittest.main()
