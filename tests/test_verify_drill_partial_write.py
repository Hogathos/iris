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
  expected: Iris says Unverified, or catches the mismatch. It must not
      say Correct.
  check: read the verdict and the journal. Pass if Unverified or
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
from devpanel import states as states_module

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


def verdict_row(rows, verdict):
    """The verdict row for one Verify, found by its reason, not by position.

    verify_read_back journals two outcome-"verified" rows: the call
    row first, then the verdict row. verified[0] is the call row, so
    a test that reads the verdict by position reads the wrong row
    (H3). The reason is the honest selector: "verify {verdict}".
    """
    for row in rows:
        if row["reason"] == f"verify {verdict}":
            return row
    return None


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

    def write_config_text(self, name, text):
        """Write one config file as raw text, synced, LF endings.

        newline="\\n" pins the line endings on every platform: on
        Windows a plain text-mode open would write \\r\\n (H9).
        """
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8", newline="\n") as proof:
            proof.write(text)
            proof.flush()
            os.fsync(proof.fileno())
        return path

    def write_config(self, name, settings):
        """Write one config file the way a real agent would: synced."""
        return self.write_config_text(name, config_text(settings))

    def write_bytes(self, name, data):
        """Write one file as raw bytes, synced. For the invalid-UTF-8 fault."""
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as proof:
            proof.write(data)
            proof.flush()
            os.fsync(proof.fileno())
        return path

    def config_claim(self, path, key, settings=None):
        """The Verify-engine claim: the config holds the given settings (default: all 10)."""
        return {
            "kind": "local-file",
            "path": path,
            "expected_text": config_text(settings if settings is not None else SETTINGS),
            "idempotency_key": key,
        }

    def test_drill_definition(self):
        """Criterion 6: Drill 1 exists with its five variables."""
        drill = library_module.get_drill("partial-write")
        self.assertEqual(drill["name"], "Partial write")
        for field in ("task", "fault", "expected", "check"):
            self.assertIn(field, drill, f"drill missing {field}")
            self.assertTrue(drill[field], f"drill {field} must not be empty")
        # H1 (D1): expected is data in the engine's words, not prose.
        # "Unknown" is not a verdict; the engine returns Unverified.
        expected = drill["expected"]
        self.assertIsInstance(expected, dict, "expected is data, not prose")
        self.assertEqual(expected.get("allowed"), ["Unverified", "Incorrect"])
        self.assertEqual(expected.get("forbidden"), ["Correct"])
        self.assertNotIn("Unknown", str(expected), "the engine never returns Unknown")
        for field in ("source", "license"):
            self.assertIn(field, drill["check"], f"drill check missing {field}")
            self.assertTrue(drill["check"][field], f"drill check {field} must not be empty")
        # H5: the drill's check is a catalog check, not a bare dict
        # that never passed load_catalog.
        self.assertIn("id", drill["check"], "the drill check has an id")
        self.assertEqual(library_module.get_check(drill["check"]["id"]), drill["check"])
        with self.assertRaises(ValueError):
            library_module.get_drill("no-such-drill")

    def test_drill_partial_write_verdict_is_not_correct(self):
        """Criterion 6 (H2, D2): 3 of 10 settings gone, the verdict is Incorrect.

        The file is readable, the done clause is approved, and the
        content differs, so criterion 3 allows exactly one verdict:
        Incorrect. Unverified would mean the read failed, which it
        did not. The allowed and forbidden sets come from the drill's
        own expected data (H1), so the data is what gets executed.
        """
        drill = library_module.get_drill("partial-write")
        trace = "drill-partial-write"
        before = self.echo.count
        # The agent writes the config with all 10 settings, synced.
        path = self.write_config("agent-config.ini", SETTINGS)
        # The fault: 3 settings go missing, like a write that half-failed.
        remaining = [s for s in SETTINGS if s.split(" = ")[0] not in REMOVED]
        self.assertEqual(len(remaining), 7, "the fault removes exactly 3 settings")
        self.write_config("agent-config.ini", remaining)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, drill["check"], self.config_claim(path, "drill-partial-1"), done_clause=DONE_CONFIG
        )
        self.assertEqual(verdict, "Incorrect", "a readable file with different content contradicts the claim")
        self.assertIn(verdict, drill["expected"]["allowed"])
        self.assertNotIn(verdict, drill["expected"]["forbidden"])
        self.assertEqual(card["verdict"], verdict)
        # The mismatch was caught, so the Run stops, like any
        # Incorrect verdict: no new tunnel moves bytes.
        self.assertTrue(self.proxy.state.is_paused(trace), "Incorrect verdict must stop the Run")
        held, status = self.open_tunnel(self.dest, trace)
        held.close()
        self.assertEqual(status, 503, "stopped Run moves no bytes")
        self.assertEqual(self.echo.count, before, "nothing reached upstream")
        self.assertEqual(
            states_module.run_state(self.rows_for(trace)),
            "PAUSED",
            "an Incorrect verdict leaves the Run paused (N9)",
        )

    def test_drill_fault_variants_never_correct(self):
        """Criterion 6 (H2): every fault shape reads Incorrect, never Correct.

        Each variant writes the config a different wrong way and
        claims all 10 settings. A readable file with different content
        is a contradiction (Incorrect); only a read that itself fails
        is Unverified. Two cases the review flagged [RUN] are
        corrected here by reading _read_back_file: a missing file is
        Incorrect (FileNotFoundError is a contradiction, not a failed
        read), and invalid UTF-8 raises out of _read_back, leaving a
        call row with no verdict row.
        """
        drill = library_module.get_drill("partial-write")
        full = config_text(SETTINGS)
        variants = [
            ("drop-middle", config_text([s for s in SETTINGS if s.split(" = ")[0] not in REMOVED]), "Incorrect"),
            ("truncate-7", config_text(SETTINGS[:7]), "Incorrect"),
            ("cut-mid-line", full.replace("setting_05 = value_05", "setting_05 = value_0"), "Incorrect"),
            ("empty", "", "Incorrect"),
        ]
        for name, text, expected in variants:
            with self.subTest(variant=name):
                trace = f"drill-variant-{name}"
                path = self.write_config_text(f"variant-{name}.ini", text)
                claim = self.config_claim(path, f"drill-variant-{name}-1")
                verdict, _ = library_module.run_check(
                    self.proxy.state, trace, drill["check"], claim, done_clause=DONE_CONFIG
                )
                self.assertEqual(verdict, expected, f"fault {name} must read {expected}")
                self.assertNotEqual(verdict, "Correct")
        # A missing file: Incorrect, not Unverified.
        with self.subTest(variant="missing"):
            trace = "drill-variant-missing"
            path = os.path.join(self.tmp.name, "no-such-config.ini")
            claim = self.config_claim(path, "drill-variant-missing-1")
            verdict, _ = library_module.run_check(
                self.proxy.state, trace, drill["check"], claim, done_clause=DONE_CONFIG
            )
            self.assertEqual(verdict, "Incorrect", "a missing file contradicts the claim")
            self.assertTrue(self.proxy.state.is_paused(trace), "Incorrect verdict must stop the Run")
        # Invalid UTF-8: the read raises, so there is no verdict at
        # all. The call row is journaled, the verdict row is not, and
        # the Run is not stopped. The byte 0x81 is invalid under both
        # UTF-8 and cp1252, the two likely default encodings, so this
        # behaves the same on Linux and on Windows.
        with self.subTest(variant="invalid-utf8"):
            trace = "drill-variant-invalid-utf8"
            path = self.write_bytes("variant-invalid-utf8.ini", b"setting_01 = value_\x81\n")
            claim = self.config_claim(path, "drill-variant-invalid-utf8-1")
            with self.assertRaises(UnicodeDecodeError):
                library_module.run_check(
                    self.proxy.state, trace, drill["check"], claim, done_clause=DONE_CONFIG
                )
            rows = self.rows_for(trace)
            call_reason = "verify call drill-variant-invalid-utf8-1"
            call_rows = [r for r in rows if r["reason"] == call_reason]
            self.assertEqual(len(call_rows), 1, "the call row is journaled before the read")
            verdict_rows = [r for r in rows if r["outcome"] == "verified" and r["reason"] != call_reason]
            self.assertEqual(verdict_rows, [], "a raised read leaves no verdict row")
            self.assertFalse(self.proxy.state.is_paused(trace), "no verdict means no stop")

    def test_drill_fault_matching_claim_is_correct(self):
        """Criterion 6 (H2): the verdict follows the claim, not a hard-coded "10".

        Write the 7-line file the fault leaves behind, and claim
        those same 7 lines. The read matches, so the verdict is
        Correct. If the drill hard-coded "10 settings", this would
        read Incorrect.
        """
        drill = library_module.get_drill("partial-write")
        trace = "drill-matching-claim"
        remaining = [s for s in SETTINGS if s.split(" = ")[0] not in REMOVED]
        path = self.write_config("matching-config.ini", remaining)
        claim = self.config_claim(path, "drill-matching-1", settings=remaining)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, drill["check"], claim, done_clause=DONE_CONFIG
        )
        self.assertEqual(verdict, "Correct", "the verdict follows the claim, not the number 10")
        self.assertEqual(card["verdict"], "Correct")
        self.assertFalse(self.proxy.state.is_paused(trace))

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
        self.assertEqual(
            states_module.run_state(self.rows_for(trace)),
            "FLOWING",
            "a Correct verdict leaves the Run flowing (N9)",
        )

    def test_drill_journals_the_verdict(self):
        """Criterion 6: the drill's verdict is journaled, with provenance.

        Pinned to dedicated columns, not joined row text (H3): the
        verdict row, found by its reason, holds the drill check's
        exact source and license in check_source and check_license,
        and the call row carries the same stamp (D5).
        """
        drill = library_module.get_drill("partial-write")
        trace = "drill-partial-journal"
        path = self.write_config("journal-config.ini", SETTINGS)
        remaining = [s for s in SETTINGS if s.split(" = ")[0] not in REMOVED]
        self.write_config("journal-config.ini", remaining)
        verdict, _ = library_module.run_check(
            self.proxy.state, trace, drill["check"], self.config_claim(path, "drill-journal-1"), done_clause=DONE_CONFIG
        )
        self.assertEqual(verdict, "Incorrect", "the fault is a readable mismatch")
        rows = self.rows_for(trace)
        verified = [row for row in rows if row["outcome"] == "verified"]
        self.assertEqual(len(verified), 2, "one call row and one verdict row")
        row = verdict_row(rows, "Incorrect")
        self.assertIsNotNone(row, "the verdict row is found by its reason, not by position")
        self.assertEqual(row["check_source"], drill["check"]["source"], "journal must record the drill check's source")
        self.assertEqual(row["check_license"], drill["check"]["license"], "journal must record the drill check's license")
        for target in verified:
            self.assertEqual(target["check_source"], drill["check"]["source"], "both Verify rows carry the stamp (D5)")
            self.assertEqual(target["check_license"], drill["check"]["license"], "both Verify rows carry the stamp (D5)")


if __name__ == "__main__":
    unittest.main()
