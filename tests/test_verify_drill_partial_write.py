"""Drill 1: Partial write, end to end.

Test-first: this file FAILS until verify_library exists. The failure is
the missing feature (ModuleNotFoundError for verify_library), not a
typo: every call below matches a real API -- verify_read_back on the
proxy state, the journal in db.py -- except the library's drill, which
is the feature under test.

The drill, in the five variables the filed material holds:
  name: Partial write.
  task: the agent writes a config file with 10 settings.
  fault: after it writes, the drill removes 3 settings, like a write
      that half-failed.
  expected: Iris says Unknown, or catches the mismatch. It must not
      say Correct.
  check: read the verdict and the journal. Pass if Unknown or
      Incorrect, fail if Correct.
The drill is agenticized: the library provides it as data (get_drill)
and runs it through the Verify engine (run_check). The test plays the
agent (writes the file) and the drill's fault (removes 3 settings), so
the test controls the evidence and the verdict is the thing proved.

This drill also checks Iris's own record, not just the agent's claim.
If the journal has a gap, does Iris notice the gap, or does it trust
the incomplete journal and call it Correct? Here the gap is in the
file, and the read-back must catch it.

Charles's framing: these tests are based on the evidence outlined and
as verifiable as they can be. If something breaks, that is a discovery,
not a defect. A test that proves the implementation wrong is the point.

Loopback only. Metadata only in journal rows. No bodies, no keys, no
Authorization headers, no real hostnames. No scores, no percentages,
no "probably correct".
"""

import os
import socket
import socketserver
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import proxy as proxy_module
import verify_library as library_module

# Human-approved "done" clause for this test, once per task type,
# as a named read-back, fixed before the Run starts.
DONE_CONFIG = {
    "task_type": "config file write",
    "clause": "10 settings present after close/fsync",
    "read_back": "named read-back: config file has 10 settings after close/fsync",
    "approved_by": "human",
}

# The agent's config: 10 settings, one per line.
SETTINGS = [f"setting_{i:02d} = value_{i:02d}" for i in range(1, 11)]
# The drill's fault: settings 03, 06, and 09 go missing, like a write
# that half-failed. Seven settings remain.
REMOVED = {"setting_03", "setting_06", "setting_09"}


def config_text(settings):
    """The config file's text for one list of settings, one per line."""
    return "\n".join(settings) + "\n"


class _CountingEchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream that counts its own connections."""

    def handle(self):
        self.server.count += 1
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class PartialWriteDrillTest(unittest.TestCase):
    """Drill 1: a half-written config must never read back Correct."""

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

    def write_config(self, name, settings):
        """Write one config file the way a real agent would: synced."""
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as proof:
            proof.write(config_text(settings))
            proof.flush()
            os.fsync(proof.fileno())
        return path

    def config_claim(self, path, key):
        """The Verify-engine claim: the config holds all 10 settings."""
        return {
            "kind": "local-file",
            "path": path,
            "expected_text": config_text(SETTINGS),
            "idempotency_key": key,
        }

    def test_drill_definition(self):
        """Criterion 6: Drill 1 exists with its five variables."""
        drill = library_module.get_drill("partial-write")
        self.assertEqual(drill["name"], "Partial write")
        for field in ("task", "fault", "expected", "check"):
            self.assertIn(field, drill, f"drill missing {field}")
            self.assertTrue(drill[field], f"drill {field} must not be empty")
        self.assertIn("Unknown", drill["expected"])
        self.assertIn("Incorrect", drill["expected"])
        self.assertIn("never Correct", drill["expected"])
        for field in ("source", "license"):
            self.assertIn(field, drill["check"], f"drill check missing {field}")
            self.assertTrue(drill["check"][field], f"drill check {field} must not be empty")
        with self.assertRaises(ValueError):
            library_module.get_drill("no-such-drill")

    def test_drill_partial_write_verdict_is_not_correct(self):
        """Criterion 6: 3 of 10 settings gone, the verdict is never Correct."""
        drill = library_module.get_drill("partial-write")
        trace = "drill-partial-write"
        # The agent writes the config with all 10 settings, synced.
        path = self.write_config("agent-config.ini", SETTINGS)
        # The fault: 3 settings go missing, like a write that half-failed.
        remaining = [s for s in SETTINGS if s.split(" = ")[0] not in REMOVED]
        self.assertEqual(len(remaining), 7, "the fault removes exactly 3 settings")
        self.write_config("agent-config.ini", remaining)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, drill["check"], self.config_claim(path, "drill-partial-1"), done_clause=DONE_CONFIG
        )
        self.assertIn(verdict, ("Unknown", "Incorrect"), "the drill must not read back Correct")
        self.assertNotEqual(verdict, "Correct", "a half-written config is never Correct")
        self.assertEqual(card["verdict"], verdict)
        if verdict == "Incorrect":
            # The mismatch was caught, so the Run stops, like any
            # Incorrect verdict: no new tunnel moves bytes.
            self.assertTrue(self.proxy.state.is_paused(trace), "Incorrect verdict must stop the Run")
            held, status = self.open_tunnel(self.dest, trace)
            held.close()
            self.assertEqual(status, 503, "stopped Run moves no bytes")

    def test_drill_control_full_write_is_correct(self):
        """Criterion 6, the control: no fault, all 10 settings, Correct.

        This proves the fault is what makes the drill fail, not the
        drill itself. If the drill always said Incorrect, this fails.
        """
        drill = library_module.get_drill("partial-write")
        trace = "drill-partial-control"
        path = self.write_config("control-config.ini", SETTINGS)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, drill["check"], self.config_claim(path, "drill-control-1"), done_clause=DONE_CONFIG
        )
        self.assertEqual(verdict, "Correct")
        self.assertEqual(card["verdict"], "Correct")
        self.assertFalse(self.proxy.state.is_paused(trace), "Correct verdict must not stop the Run")

    def test_drill_journals_the_verdict(self):
        """Criterion 6: the drill's verdict is journaled, with provenance."""
        drill = library_module.get_drill("partial-write")
        trace = "drill-partial-journal"
        path = self.write_config("journal-config.ini", SETTINGS)
        remaining = [s for s in SETTINGS if s.split(" = ")[0] not in REMOVED]
        self.write_config("journal-config.ini", remaining)
        verdict, _ = library_module.run_check(
            self.proxy.state, trace, drill["check"], self.config_claim(path, "drill-journal-1"), done_clause=DONE_CONFIG
        )
        self.assertIn(verdict, ("Unknown", "Incorrect"))
        rows = self.rows_for(trace)
        verdict_rows = [r for r in rows if r["outcome"] == "verified"]
        self.assertTrue(verdict_rows, "the drill verdict must be journaled")
        text = " ".join(str(row[key]) for row in rows for key in row.keys())
        self.assertIn(drill["check"]["source"], text, "journal must record the drill check's source")
        self.assertIn(drill["check"]["license"], text, "journal must record the drill check's license")


if __name__ == "__main__":
    unittest.main()
