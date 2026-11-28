"""Verify check library: catalog, secrets profile, verdicts, provenance.

Test-first: this file FAILS until verify_library exists. The failure is
the missing feature (ModuleNotFoundError for verify_library), not a
typo: every call below matches a real API -- verify_read_back on the
proxy state, the journal in db.py -- except the library itself, which
is the feature under test.

The library contract these tests pin (the implementer builds to it):
  load_catalog(checks=None) -> list of check dicts; no args gives the
      built-in seed catalog. A check missing a non-empty source or
      license raises ValueError and does not load.
  get_check(check_id) -> one check dict, or None.
  load_profile(name) -> the checks in one lane; an unknown lane raises
      ValueError.
  run_check(state, trace_id, check, claim, done_clause=None) ->
      (verdict, card). Runs the check through the Verify engine
      (state.verify_read_back) and journals the check's source and
      license with the verdict.
A check is structured data: id, name, claim (plain words), steps,
evidence (what evidence counts), source, license.

Charles's framing: these tests are based on the evidence outlined and
as verifiable as they can be. If something breaks, that is a discovery,
not a defect. A test that proves the implementation wrong is the point.

Loopback only. Metadata only in journal rows. No bodies, no keys, no
Authorization headers, no real hostnames. No scores, no percentages,
no "probably correct". The fake secret below is clearly fake and never
a real key; it must never appear in the journal.
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
DONE_FILE = {
    "task_type": "local file write",
    "clause": "row exists after close/fsync",
    "read_back": "named read-back: file row exists after close/fsync",
    "approved_by": "human",
}

# A fake secret, clearly fake, never a real key. It must never appear
# in the journal: no bodies, no keys.
FAKE_SECRET = "sk-test-fake-00000"

# The log the agent should have written: the secret redacted.
REDACTED_LOG = "2026-11-28T08:00:00Z INFO request ok api_key=[REDACTED]\n"
# The log the agent must never have written: the raw secret in the clear.
RAW_LOG = f"2026-11-28T08:00:00Z INFO request ok api_key={FAKE_SECRET}\n"


class _CountingEchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream that counts its own connections."""

    def handle(self):
        self.server.count += 1
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class VerifyCheckLibraryTest(unittest.TestCase):
    """The check catalog, the secrets profile, verdicts, provenance."""

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

    def row_text_for(self, trace_id):
        """Every journal value for one trace_id, as one searchable text."""
        text = []
        for row in self.rows_for(trace_id):
            text.extend(str(row[key]) for key in row.keys())
        return " ".join(text)

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

    def write_log(self, name, text):
        """Write one log file the way a real agent would: synced."""
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as proof:
            proof.write(text)
            proof.flush()
            os.fsync(proof.fileno())
        return path

    def log_claim(self, path, key):
        """The Verify-engine claim for one log file, redacted text expected."""
        return {
            "kind": "local-file",
            "path": path,
            "expected_text": REDACTED_LOG,
            "idempotency_key": key,
        }

    def test_catalog_holds_checks_as_structured_data(self):
        """Criterion 1: each check is claim, steps, evidence, source, license."""
        catalog = library_module.load_catalog()
        self.assertTrue(catalog, "the seed catalog must not be empty")
        for check in catalog:
            for field in ("id", "name", "claim", "steps", "evidence", "source", "license"):
                self.assertIn(field, check, f"check missing {field}")
                self.assertTrue(check[field], f"check {field} must not be empty")
            self.assertIsInstance(check["steps"], list, "steps is a list")
            self.assertTrue(len(check["steps"]) >= 1, "a check has at least one step")
            self.assertNotIn("probably", check["claim"].lower())
            self.assertNotIn("%", check["claim"])
        ids = [check["id"] for check in catalog]
        self.assertEqual(len(ids), len(set(ids)), "check ids are unique")

    def test_check_missing_source_or_license_does_not_load(self):
        """Criterion 1: a check with no source or license is refused."""
        good = {
            "id": "test-check",
            "name": "Test check",
            "claim": "the thing happened",
            "steps": ["read it back"],
            "evidence": "the read-back",
            "source": "Test source",
            "license": "MIT",
        }
        self.assertEqual(len(library_module.load_catalog([good])), 1)
        no_license = dict(good, id="no-license")
        del no_license["license"]
        with self.assertRaises(ValueError, msg="a check with no license must not load"):
            library_module.load_catalog([no_license])
        no_source = dict(good, id="no-source")
        del no_source["source"]
        with self.assertRaises(ValueError, msg="a check with no source must not load"):
            library_module.load_catalog([no_source])
        empty_license = dict(good, id="empty-license", license="")
        with self.assertRaises(ValueError, msg="a check with an empty license must not load"):
            library_module.load_catalog([empty_license])
        empty_source = dict(good, id="empty-source", source="   ")
        with self.assertRaises(ValueError, msg="a check with a blank source must not load"):
            library_module.load_catalog([empty_source])
        # One bad check refuses the whole load, not just itself.
        with self.assertRaises(ValueError):
            library_module.load_catalog([good, no_license])

    def test_secrets_profile_contains_secret_redaction_check(self):
        """Criterion 2: the secrets lane holds the SandHive redaction check."""
        profile = library_module.load_profile("secrets")
        self.assertTrue(profile, "the secrets profile must not be empty")
        check = library_module.get_check("secret-redaction-in-logs")
        self.assertIsNotNone(check, "the redaction check must exist")
        self.assertIn(check, profile, "the redaction check belongs to the secrets profile")
        self.assertIn("SandHive", check["source"], "the check names its source")
        self.assertEqual(check["license"], "MIT", "SandHive materials are MIT")
        self.assertIn("secret", check["claim"].lower(), "the claim is about secrets")
        self.assertIn("redact", check["claim"].lower(), "the claim is about redaction")
        self.assertIsNone(library_module.get_check("no-such-check"))

    def test_unknown_profile_is_refused(self):
        """A lane that does not exist is refused, not silently empty."""
        with self.assertRaises(ValueError):
            library_module.load_profile("no-such-lane")

    def test_library_check_correct_when_read_matches(self):
        """Criterion 3: redacted log matches the claim, verdict Correct."""
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-correct-redacted"
        path = self.write_log("redacted.log", REDACTED_LOG)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-correct-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Correct")
        self.assertEqual(card["verdict"], "Correct")
        self.assertFalse(self.proxy.state.is_paused(trace), "Correct verdict must not stop the Run")
        flowing, status = self.open_tunnel(self.dest, trace)
        try:
            self.assertEqual(status, 200, "Correct Run still flows")
            flowing.sendall(b"12345")
            self.assertEqual(flowing.recv(65536), b"12345")
        finally:
            flowing.close()

    def test_library_check_incorrect_when_secret_leaks(self):
        """Criterion 3: raw secret in the log contradicts the claim, Run stops."""
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-incorrect-leak"
        path = self.write_log("raw.log", RAW_LOG)
        before = self.echo.count
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-incorrect-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Incorrect")
        self.assertEqual(card["verdict"], "Incorrect")
        self.assertTrue(self.proxy.state.is_paused(trace), "Incorrect verdict must stop the Run")
        bad_rows = [r for r in self.rows_for(trace) if r["outcome"] == "verified" and r["status"] == "failure"]
        self.assertTrue(bad_rows, "Incorrect verdict must be journaled")
        held, status = self.open_tunnel(self.dest, trace)
        held.close()
        self.assertEqual(status, 503, "stopped Run moves no bytes")
        self.assertEqual(self.echo.count, before, "nothing reached upstream")

    def test_library_check_unverified_when_read_cannot_be_done(self):
        """Criterion 3: no done, unreadable file, remote: Unverified, Run flows."""
        check = library_module.get_check("secret-redaction-in-logs")
        # No approved done clause: Unverified, never Correct.
        trace = "lib-unverified-no-done"
        path = self.write_log("nodone.log", REDACTED_LOG)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-unknown-1"), done_clause=None
        )
        self.assertEqual(verdict, "Unverified")
        self.assertEqual(card["verdict"], "Unverified")
        self.assertFalse(self.proxy.state.is_paused(trace), "Unverified verdict must not stop the Run")
        # Unreadable file: a directory, not a file. Unverified, not a
        # contradiction: the read failed, it did not contradict the claim.
        trace = "lib-unverified-unreadable"
        dir_path = os.path.join(self.tmp.name, "a-directory")
        os.mkdir(dir_path)
        verdict, _ = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(dir_path, "lib-unknown-2"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Unverified")
        self.assertFalse(self.proxy.state.is_paused(trace))
        # Remote claim with no local proof: Unverified, even with an
        # approved done clause.
        trace = "lib-unverified-remote"
        remote_claim = {
            "kind": "remote",
            "system": "stripe",
            "reference": "pi_test_123",
            "idempotency_key": "lib-unknown-3",
        }
        verdict, _ = library_module.run_check(
            self.proxy.state, trace, check, remote_claim, done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Unverified", "remote with no local proof stays Unverified")
        self.assertFalse(self.proxy.state.is_paused(trace))

    def test_journal_records_check_source_and_license(self):
        """Criterion 4: the journal carries each check's source and license.

        Pinned to dedicated columns, not "somewhere in the row": the
        attempts table carries check_source and check_license, and the
        verdict row for this trace holds the check's exact source and
        license there. A check_source or check_license that does not
        exist, or that holds the wrong value, fails this test. The
        implementer adds the two columns to the attempts table; until
        then this test fails with a missing-column error, which is the
        test-first signal, not a typo.
        """
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-provenance"
        path = self.write_log("prov.log", RAW_LOG)
        verdict, _ = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-prov-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Incorrect")
        rows = self.rows_for(trace)
        verified = [row for row in rows if row["outcome"] == "verified"]
        self.assertTrue(verified, "the verdict must be journaled")
        row = verified[0]
        self.assertEqual(row["check_source"], check["source"], "journal must record the check's source in check_source")
        self.assertEqual(row["check_license"], check["license"], "journal must record the check's license in check_license")
        # The raw secret itself must never reach the journal: no bodies,
        # no keys, even when the file under check contains one.
        text = self.row_text_for(trace)
        self.assertNotIn(FAKE_SECRET, text, "no bodies in the journal")


if __name__ == "__main__":
    unittest.main()
