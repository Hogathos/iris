"""Verify incorrect stops, live cut and controls — TEST ONLY.

Problem in one sentence: an Incorrect verdict must stop the Run,
and a Correct or Unverified verdict must not.

What the existing test already pins: an Incorrect verdict on a Run
with no live tunnel stops it, and a new tunnel gets 503. See
tests/test_verify_read_back_local.py, step 4.

What this file pins, and the existing test does not:
  1. An Incorrect verdict on a LIVE Run cuts the live tunnel. The
     close row reads paused, status unknown, bytes kept, reason
     "tunnel cut by pause". The Run stays held after the cut.
  2. A Correct verdict does not stop the Run. Traffic still flows.
  3. An Unverified verdict does not stop the Run. Traffic still
     flows.
  4. The panel reads the stop right: the verdict is Incorrect, and
     the Run folds to PAUSED.

Test-first history: Charles reviews this test in Main first. A fix
prompt follows only if a check fails. The stop itself is already
built (verify_read_back pauses the Run on Incorrect), so this file
will likely pass with no production fix. Say so plainly if that is
what the run shows.

Plain words: Verify means check what the agent said it did,
against what actually happened, by reading it back. Incorrect
means the read contradicted the claim. The Run stops. A human
reviews the claim.

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
from devpanel import states as states_module

# Human-approved "done" clause for this test, once per task type,
# as a named read-back, fixed before the Run starts.
DONE_FILE = {
    "task_type": "local file write",
    "clause": "row exists after close/fsync",
    "read_back": "named read-back: file row exists after close/fsync",
    "approved_by": "human",
}

STEP_TIMEOUT = 10.0


class _CountingEchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream that counts its own connections."""

    def handle(self):
        self.server.count += 1
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class VerifyIncorrectStopsTest(unittest.TestCase):
    """An Incorrect verdict stops the Run; Correct and Unverified do not."""

    @classmethod
    def setUpClass(cls):
        """Start one loopback echo destination and one proxy on temp db."""
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _CountingEchoHandler)
        cls.echo.count = 0
        threading.Thread(target=cls.echo.serve_forever, daemon=True).start()
        cls.proxy = proxy_module.serve("127.0.0.1", 0, cls.db_path)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        cls.dest = f"127.0.0.1:{cls.echo.server_address[1]}"

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
        sock.sendall((f"CONNECT {dest} HTTP/1.1\r\nHost: {dest}\r\nX-Trace-Id: {trace_id}\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            self.assertTrue(chunk, "proxy never answered CONNECT")
            raw += chunk
        head = raw.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
        return sock, int(head.split()[1])

    def open_and_exchange(self, trace_id, payload=b"12345"):
        """Open one tunnel and exchange bytes; return the socket."""
        sock, status = self.open_tunnel(self.dest, trace_id)
        self.assertEqual(status, 200, "tunnel must open before the check")
        sock.sendall(payload)
        self.assertEqual(sock.recv(65536), payload, "echo must answer")
        return sock

    def wait_for_row(self, trace_id, match, timeout=STEP_TIMEOUT):
        """Wait for a journal row that matches; fail loudly if none."""
        deadline = time.monotonic() + timeout
        while True:
            rows = [r for r in self.rows_for(trace_id) if match(r)]
            if rows or time.monotonic() >= deadline:
                return rows
            time.sleep(0.02)

    def test_incorrect_cuts_live_tunnel(self):
        """Incorrect on a live Run cuts the live tunnel and holds it."""
        trace = "verify-incorrect-live"
        file_path = os.path.join(self.tmp.name, "agent-effect.txt")
        with open(file_path, "w") as proof:
            proof.write("Alice")
            proof.flush()
            os.fsync(proof.fileno())
        # The Run is live when the verdict lands. The stop must cut it.
        # The echo count is shared across the checks in this file, so
        # pin the delta, not an absolute number.
        before = self.echo.count
        sock = self.open_and_exchange(trace)
        try:
            claim = {
                "kind": "local-file",
                "path": file_path,
                "expected_text": "Bob",
                "idempotency_key": "verify-incorrect-live-1",
            }
            verdict, card = self.proxy.state.verify_read_back(trace, claim, done_clause=DONE_FILE)
            self.assertEqual(verdict, "Incorrect")
            self.assertEqual(card["verdict"], "Incorrect")
            # The verdict row is journaled before the stop follows it.
            verdict_rows = [r for r in self.rows_for(trace) if r["outcome"] == "verified" and r["status"] == "failure"]
            self.assertTrue(verdict_rows, "Incorrect verdict must be journaled")
            self.assertTrue(self.proxy.state.is_paused(trace), "Incorrect verdict must stop the Run")
            # The cut tunnel's own close row lands on the handler's
            # thread, so wait for it with a deadline.
            cut_rows = self.wait_for_row(trace, lambda r: r["outcome"] == "paused" and r["reason"] == "tunnel cut by pause")
            self.assertTrue(cut_rows, "cut tunnel must journal its close row")
            cut_row = cut_rows[0]
            self.assertEqual(cut_row["dest_host"], self.dest, "cut row must name the live destination")
            self.assertEqual(cut_row["status"], "unknown")
            self.assertEqual(cut_row["bytes_tx"], 5, "bytes kept, not dropped")
            self.assertEqual(cut_row["bytes_rx"], 5, "bytes kept, not dropped")
            self.assertLess(verdict_rows[0]["id"], cut_row["id"], "verdict row first, stop after")
            # The Run stays held: a new tunnel gets 503, and nothing
            # more reaches upstream.
            held, status = self.open_tunnel(self.dest, trace)
            held.close()
            self.assertEqual(status, 503, "stopped Run moves no bytes")
            self.assertEqual(self.echo.count, before + 1, "only the first tunnel reached upstream")
        finally:
            sock.close()

    def test_correct_does_not_stop(self):
        """Correct verdict: the Run keeps flowing."""
        trace = "verify-correct-flows"
        file_path = os.path.join(self.tmp.name, "agent-effect.txt")
        with open(file_path, "w") as proof:
            proof.write("Alice")
            proof.flush()
            os.fsync(proof.fileno())
        sock = self.open_and_exchange(trace)
        sock.close()
        claim = {
            "kind": "local-file",
            "path": file_path,
            "expected_text": "Alice",
            "idempotency_key": "verify-correct-flows-1",
        }
        verdict, card = self.proxy.state.verify_read_back(trace, claim, done_clause=DONE_FILE)
        self.assertEqual(verdict, "Correct")
        self.assertFalse(self.proxy.state.is_paused(trace), "Correct verdict must not stop the Run")
        before = self.echo.count
        flowing, status = self.open_tunnel(self.dest, trace)
        try:
            self.assertEqual(status, 200, "Correct Run still flows")
            flowing.sendall(b"12345")
            self.assertEqual(flowing.recv(65536), b"12345")
        finally:
            flowing.close()
        self.assertEqual(self.echo.count, before + 1, "Correct Run must reach upstream")

    def test_unverified_does_not_stop(self):
        """Unverified verdict: the Run keeps flowing."""
        trace = "verify-unverified-flows"
        file_path = os.path.join(self.tmp.name, "agent-effect.txt")
        with open(file_path, "w") as proof:
            proof.write("Alice")
            proof.flush()
            os.fsync(proof.fileno())
        sock = self.open_and_exchange(trace)
        sock.close()
        claim = {
            "kind": "local-file",
            "path": file_path,
            "expected_text": "Alice",
            "idempotency_key": "verify-unverified-flows-1",
        }
        verdict, card = self.proxy.state.verify_read_back(trace, claim, done_clause=None)
        self.assertEqual(verdict, "Unverified")
        self.assertFalse(self.proxy.state.is_paused(trace), "Unverified verdict must not stop the Run")
        before = self.echo.count
        flowing, status = self.open_tunnel(self.dest, trace)
        try:
            self.assertEqual(status, 200, "Unverified Run still flows")
            flowing.sendall(b"12345")
            self.assertEqual(flowing.recv(65536), b"12345")
        finally:
            flowing.close()
        self.assertEqual(self.echo.count, before + 1, "Unverified Run must reach upstream")

    def test_panel_reads_incorrect_stop(self):
        """Panel: the verdict is Incorrect, the Run folds to PAUSED."""
        # Hand-built rows: call, verdict, then the stop that follows it.
        built = [
            {"outcome": "verified", "status": "unknown", "reason": "verify call k1"},
            {"outcome": "verified", "status": "failure", "reason": "verify Incorrect"},
            {"outcome": "paused", "status": "unknown", "reason": "Steer pause"},
        ]
        self.assertEqual(states_module.verdict_for(built), "Incorrect")
        self.assertEqual(states_module.run_state(built), "PAUSED")
        # Real rows: this test runs its own Incorrect verdict, so it
        # does not depend on another check having run first.
        trace = "verify-incorrect-panel"
        file_path = os.path.join(self.tmp.name, "agent-effect.txt")
        with open(file_path, "w") as proof:
            proof.write("Alice")
            proof.flush()
            os.fsync(proof.fileno())
        claim = {
            "kind": "local-file",
            "path": file_path,
            "expected_text": "Bob",
            "idempotency_key": "verify-incorrect-panel-1",
        }
        verdict, _ = self.proxy.state.verify_read_back(trace, claim, done_clause=DONE_FILE)
        self.assertEqual(verdict, "Incorrect")
        rows = self.rows_for(trace)
        self.assertTrue(rows, "panel Run must have journal rows")
        self.assertEqual(states_module.verdict_for(rows), "Incorrect")
        self.assertEqual(states_module.run_state(rows), "PAUSED")


if __name__ == "__main__":
    unittest.main()
