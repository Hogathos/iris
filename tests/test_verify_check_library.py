"""Verify check library: catalog, secrets profile, verdicts, provenance.

Test-first: this file FAILS until verify_library exists. The failure is
the missing feature (ModuleNotFoundError for verify_library), not a
typo: every call below matches a real API -- verify_read_back on the
proxy state, the journal in db.py -- except the library itself, which
is the feature under test.

The library contract these tests pin (the implementer builds to it):
  load_catalog(checks=None) -> list of check dicts; no args gives the
      built-in seed catalog. A check missing a non-empty string source
      or license, a non-empty list of string steps, a non-empty id,
      name, claim, or evidence raises ValueError and does not load.
      Duplicate ids raise ValueError. An explicit empty list loads
      empty, not the seed. One bad check refuses the whole load (D4).
  get_check(check_id) -> one check dict, or None. The dict returned
      is a defensive copy (N10).
  load_profile(name) -> the checks in one lane; an unknown lane raises
      ValueError. Every check in a profile is in the catalog (N12).
  run_check(state, trace_id, check, claim, done_clause=None) ->
      (verdict, card). Runs the check through the Verify engine
      (state.verify_read_back, exactly once, H6) and journals the
      check's source and license with both Verify rows. A check with
      no provenance raises ValueError before any journal row (H5).
      The claim is passed unchanged and never mutated.
  step(state, event) -> the next state, or the same state for a
      no-op pair. An invalid state raises ValueError (N4, D7).
A check is structured data: id, name, claim (plain words), steps,
evidence (what evidence counts), source, license.

The engine compares exact text: a verdict is Correct only when the
file equals the expected text, Incorrect when it differs for any
reason (a leaked secret, garbage, a different line), Unverified when
the read cannot be done. The library does not scan for secrets; it
reads the file back against the claim (H9).

Charles's framing: these tests are based on the evidence outlined and
as verifiable as they can be. If something breaks, that is a discovery,
not a defect. A test that proves the implementation wrong is the point.

Loopback only. Metadata only in journal rows. No bodies, no keys, no
Authorization headers, no real hostnames. No scores, no percentages,
no "probably correct". The fake secret below is clearly fake and never
a real key; it must never appear in the journal.
"""

import copy
import glob
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

import db as db_module
import proxy as proxy_module
import verify_library as library_module
from devpanel import states as states_module

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
# A log that differs from the redacted claim with no secret in it.
GARBAGE_LOG = "2026-11-28T08:00:00Z INFO something else entirely\n"

# The attempts table as it stands before the check columns land: 24
# columns, written out here, not derived from db.CREATE_TABLE_SQL.
# Deriving it would go vacuous the moment the implementer edits the
# DDL, and this fixture must stay the OLD schema so the migration
# test (H4) and the accounting test (H11) mean something.
OLD_ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    task_number INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    strategy TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    latency_seconds REAL NOT NULL DEFAULT 0.0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_dollars REAL NOT NULL DEFAULT 0.0,
    retry_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    dest_host TEXT NOT NULL DEFAULT '',
    endpoint TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    bytes_in INTEGER NOT NULL DEFAULT 0,
    bytes_out INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    outcome TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    bytes_tx INTEGER NOT NULL DEFAULT 0,
    bytes_rx INTEGER NOT NULL DEFAULT 0,
    close_reason TEXT NOT NULL DEFAULT '',
    connect_ms REAL,
    duration_ms REAL
);
"""


def make_check(**overrides):
    """One well-formed check, for the loader tests to break one field of."""
    check = {
        "id": "test-check",
        "name": "Test check",
        "claim": "the thing happened",
        "steps": ["read it back"],
        "evidence": "the read-back",
        "source": "Test source",
        "license": "MIT",
    }
    check.update(overrides)
    return check


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

    def wait_for_rows(self, trace_id, timeout=2.0):
        """Wait until the journal holds a row for one trace_id.

        A tunnel's row is journaled by the proxy's handler thread
        when the tunnel closes, so it can land just after the client
        socket closes. Poll briefly instead of assuming it is there.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.rows_for(trace_id):
                return
            time.sleep(0.05)
        self.fail(f"no journal row for {trace_id} after {timeout}s")

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
        """Write one log file the way a real agent would: synced, LF endings.

        newline="\\n" pins the line endings on every platform: on
        Windows a plain text-mode open would write \\r\\n, and whether
        the Correct test then passes would depend on how the engine
        reads the file (H9 platform note).
        """
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8", newline="\n") as proof:
            proof.write(text)
            proof.flush()
            os.fsync(proof.fileno())
        return path

    def write_bytes(self, name, data):
        """Write one file as raw bytes, synced. For the CRLF probe."""
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as proof:
            proof.write(data)
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

    def assert_secret_absent(self, card):
        """H8: the fake secret appears nowhere it must not.

        Every row of the whole attempts table (all traces), the raw
        bytes of the database files (attempts.db*), and the card's
        repr. The temp dir itself is NOT scanned: raw.log holds the
        secret by design, and the log files under check hold it too.
        """
        text = []
        conn = sqlite3.connect(self.db_path)
        try:
            for row in conn.execute("SELECT * FROM attempts"):
                text.extend(str(value) for value in row)
        finally:
            conn.close()
        self.assertNotIn(FAKE_SECRET, " ".join(text), "the secret must never reach the journal")
        for path in sorted(glob.glob(os.path.join(self.tmp.name, "attempts.db*"))):
            with open(path, "rb") as handle:
                self.assertNotIn(
                    FAKE_SECRET.encode("utf-8"),
                    handle.read(),
                    f"the secret must never reach {os.path.basename(path)}",
                )
        self.assertNotIn(FAKE_SECRET, repr(card), "the secret must never reach the card")

    def _old_schema_db(self, tmp, rows):
        """Build a database with the OLD attempts schema and given rows.

        rows is a list of (trace_id, outcome). Returns the db path.
        The connection is opened and closed here, so the database is
        just a file when this returns, ready to be opened through the
        init path the implementation under test must migrate.
        """
        db_path = os.path.join(tmp.name, "old.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(OLD_ATTEMPTS_DDL)
            for i, (trace_id, outcome) in enumerate(rows):
                conn.execute(
                    "INSERT INTO attempts (trace_id, task_number, attempt, status, created_at, outcome, reason)"
                    " VALUES (?, 1, 1, 'success', '2026-11-28T00:00:00+00:00', ?, '')",
                    (trace_id, outcome),
                )
            conn.commit()
        finally:
            conn.close()
        return db_path

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
        self.addCleanup(library_module.load_catalog)
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
        # Labelled policy (D4): the spec says a bad check does not
        # load; refusing the whole load is the fail-closed choice.
        with self.assertRaises(ValueError):
            library_module.load_catalog([good, no_license])

    def test_check_provenance_matrix(self):
        """Criterion 1 (H7): source and license must be non-empty strings."""
        self.addCleanup(library_module.load_catalog)
        for field in ("source", "license"):
            for i, bad in enumerate([None, "", "   ", 123, ["x"]]):
                with self.subTest(field=field, bad=repr(bad)):
                    check = make_check(id=f"bad-{field}-{i}")
                    check[field] = bad
                    with self.assertRaises(ValueError):
                        library_module.load_catalog([check])
            with self.subTest(field=field, bad="missing"):
                check = make_check(id=f"bad-{field}-missing")
                del check[field]
                with self.assertRaises(ValueError):
                    library_module.load_catalog([check])

    def test_check_with_bad_steps_does_not_load(self):
        """Criterion 1 (H7): steps is a non-empty list of strings, and the rest is non-empty."""
        self.addCleanup(library_module.load_catalog)
        for bad_steps in ([], "read it back", [""], [1]):
            with self.subTest(steps=repr(bad_steps)):
                check = make_check(id="bad-steps")
                check["steps"] = bad_steps
                with self.assertRaises(ValueError):
                    library_module.load_catalog([check])
        for field in ("id", "name", "claim", "evidence"):
            with self.subTest(field=field):
                check = make_check(id="bad-field")
                check[field] = ""
                with self.assertRaises(ValueError):
                    library_module.load_catalog([check])

    def test_duplicate_check_ids_do_not_load(self):
        """Criterion 1 (H7): two checks with one id make get_check ambiguous."""
        self.addCleanup(library_module.load_catalog)
        first = make_check(id="dup-check", license="MIT")
        second = make_check(id="dup-check", license="Apache-2.0")
        with self.assertRaises(ValueError):
            library_module.load_catalog([first, second])

    def test_load_catalog_empty_list_is_empty(self):
        """Criterion 1 (H7): an explicit empty list is empty, not the seed."""
        self.addCleanup(library_module.load_catalog)
        self.assertEqual(library_module.load_catalog([]), [])

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

    def test_profile_and_catalog_are_consistent(self):
        """N12: every secrets check is in the catalog and round-trips."""
        profile = library_module.load_profile("secrets")
        catalog = library_module.load_catalog()
        self.assertTrue(profile, "the secrets profile must not be empty")
        for check in profile:
            self.assertEqual(library_module.get_check(check["id"]), check)
            self.assertIn(check, catalog, "a profile check must appear in the catalog")

    def test_unknown_profile_is_refused(self):
        """A lane that does not exist is refused, not silently empty."""
        with self.assertRaises(ValueError):
            library_module.load_profile("no-such-lane")

    def test_get_check_returns_a_defensive_copy(self):
        """N10: mutating a returned check must not change the catalog."""
        check = library_module.get_check("secret-redaction-in-logs")
        original_source = check["source"]
        original_steps = list(check["steps"])
        check["source"] = "MUTATED"
        check["steps"].append("MUTATED STEP")
        again = library_module.get_check("secret-redaction-in-logs")
        self.assertEqual(again["source"], original_source, "mutating a returned check must not stick")
        self.assertEqual(again["steps"], original_steps, "mutating returned steps must not stick")

    def test_library_check_correct_when_read_matches(self):
        """Criterion 3: the log equals the expected redacted text, verdict Correct.

        Exact text, not a secret scan: the file's bytes equal the
        claim's expected text, so the read-back matches (H9).
        """
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-correct-redacted"
        path = self.write_log("redacted.log", REDACTED_LOG)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-correct-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Correct")
        self.assertEqual(card["verdict"], "Correct")
        self.assertFalse(self.proxy.state.is_paused(trace), "Correct verdict must not stop the Run")
        self.assertEqual(
            states_module.run_state(self.rows_for(trace)),
            "FLOWING",
            "a Correct verdict leaves the Run flowing (N9)",
        )
        flowing, status = self.open_tunnel(self.dest, trace)
        try:
            self.assertEqual(status, 200, "Correct Run still flows")
            flowing.sendall(b"12345")
            self.assertEqual(flowing.recv(65536), b"12345")
        finally:
            flowing.close()

    def test_library_check_incorrect_when_log_differs_from_expected(self):
        """Criterion 3: the log text differs from the expected redacted text, Incorrect.

        Exact text, not a secret scan: the raw log differs from the
        expected redacted text, so the read-back contradicts the
        claim. Any difference reads Incorrect, secret or not (H9).
        """
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
        self.assertEqual(
            states_module.run_state(self.rows_for(trace)),
            "PAUSED",
            "an Incorrect verdict leaves the Run paused (N9)",
        )
        held, status = self.open_tunnel(self.dest, trace)
        held.close()
        self.assertEqual(status, 503, "stopped Run moves no bytes")
        self.assertEqual(self.echo.count, before, "nothing reached upstream")
        self.assert_secret_absent(card)

    def test_non_secret_mismatch_is_also_incorrect(self):
        """H9: garbage with no secret in it still reads Incorrect. Exact text."""
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-incorrect-garbage"
        path = self.write_log("garbage.log", GARBAGE_LOG)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-garbage-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Incorrect", "any text difference is a contradiction, secret or not")
        self.assertEqual(card["verdict"], "Incorrect")
        self.assertTrue(self.proxy.state.is_paused(trace), "Incorrect verdict must stop the Run")

    def test_leak_among_correct_lines_is_incorrect(self):
        """H9: the redacted line plus one raw-secret line still reads Incorrect."""
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-incorrect-leak-line"
        leaked = REDACTED_LOG + f"2026-11-28T08:00:01Z INFO retry api_key={FAKE_SECRET}\n"
        path = self.write_log("leakline.log", leaked)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-leakline-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Incorrect", "one leaked line contradicts the redacted claim")
        self.assertEqual(card["verdict"], "Incorrect")
        self.assertTrue(self.proxy.state.is_paused(trace), "Incorrect verdict must stop the Run")
        self.assert_secret_absent(card)

    def test_library_check_unverified_when_read_cannot_be_done(self):
        """Criterion 3: no done, unreadable file, remote: Unverified, Run flows.

        Each sub-case gets its own trace and idempotency key, and runs
        under subTest (N8), so one failing sub-case does not hide the
        rest. The engine returns Unverified whenever the read cannot
        be done; it never contradicts a claim it could not read.
        """
        check = library_module.get_check("secret-redaction-in-logs")
        # No approved done clause: Unverified, never Correct.
        with self.subTest(case="no-done"):
            trace = "lib-unverified-no-done"
            path = self.write_log("nodone.log", REDACTED_LOG)
            verdict, card = library_module.run_check(
                self.proxy.state, trace, check, self.log_claim(path, "lib-unknown-1"), done_clause=None
            )
            self.assertEqual(verdict, "Unverified")
            self.assertEqual(card["verdict"], "Unverified")
            self.assertFalse(self.proxy.state.is_paused(trace), "Unverified verdict must not stop the Run")
            self.assertEqual(states_module.run_state(self.rows_for(trace)), "FLOWING")
        # Unreadable file: a directory, not a file. Unverified, not a
        # contradiction: the read failed, it did not contradict the claim.
        with self.subTest(case="unreadable"):
            trace = "lib-unverified-unreadable"
            dir_path = os.path.join(self.tmp.name, "a-directory")
            os.mkdir(dir_path)
            verdict, _ = library_module.run_check(
                self.proxy.state, trace, check, self.log_claim(dir_path, "lib-unknown-2"), done_clause=DONE_FILE
            )
            self.assertEqual(verdict, "Unverified")
            self.assertFalse(self.proxy.state.is_paused(trace))
            self.assertEqual(states_module.run_state(self.rows_for(trace)), "FLOWING")
        # Remote claim with no local proof: Unverified, even with an
        # approved done clause.
        with self.subTest(case="remote"):
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
            self.assertEqual(states_module.run_state(self.rows_for(trace)), "FLOWING")
        # Remote claim on a trace that already has traffic (N6): the
        # general remote path, not the no-trace branch.
        with self.subTest(case="remote-with-traffic"):
            trace = "lib-unverified-remote-traffic"
            sock, status = self.open_tunnel(self.dest, trace)
            try:
                self.assertEqual(status, 200, "the traffic tunnel must flow")
                sock.sendall(b"12345")
                self.assertEqual(sock.recv(65536), b"12345")
            finally:
                sock.close()
            self.wait_for_rows(trace)
            remote_claim = {
                "kind": "remote",
                "system": "stripe",
                "reference": "pi_test_456",
                "idempotency_key": "lib-unknown-4",
            }
            verdict, _ = library_module.run_check(
                self.proxy.state, trace, check, remote_claim, done_clause=DONE_FILE
            )
            self.assertEqual(verdict, "Unverified", "remote with traffic but no local proof stays Unverified")
            self.assertFalse(self.proxy.state.is_paused(trace))
            self.assertEqual(states_module.run_state(self.rows_for(trace)), "FLOWING")
        # Done clause approved by an agent, not a human (N7): Unverified.
        with self.subTest(case="agent-approved"):
            trace = "lib-unverified-agent-approved"
            path = self.write_log("agent.log", REDACTED_LOG)
            agent_done = dict(DONE_FILE, approved_by="agent")
            verdict, _ = library_module.run_check(
                self.proxy.state, trace, check, self.log_claim(path, "lib-unknown-5"), done_clause=agent_done
            )
            self.assertEqual(verdict, "Unverified", "a done clause approved by an agent is not approved")
            self.assertFalse(self.proxy.state.is_paused(trace))
            self.assertEqual(states_module.run_state(self.rows_for(trace)), "FLOWING")
        # Done clause with no read_back (N7): Unverified.
        with self.subTest(case="no-read-back"):
            trace = "lib-unverified-no-read-back"
            path = self.write_log("noreadback.log", REDACTED_LOG)
            bare_done = {key: value for key, value in DONE_FILE.items() if key != "read_back"}
            verdict, _ = library_module.run_check(
                self.proxy.state, trace, check, self.log_claim(path, "lib-unknown-6"), done_clause=bare_done
            )
            self.assertEqual(verdict, "Unverified", "a done clause with no read-back is not approved")
            self.assertFalse(self.proxy.state.is_paused(trace))
            self.assertEqual(states_module.run_state(self.rows_for(trace)), "FLOWING")

    def test_empty_expected_text_is_not_correct(self):
        """H9 probe (D3): an empty expected_text against an empty file.

        PROBE, not a guard. The engine compares exact text, so an
        empty file equals an empty expected_text and reads Correct:
        a vacuous pass, the failure Iris exists to prevent. This test
        records that behavior; it does not assert the library guards
        it, because D3 says no guard yet. When the guard lands, this
        test flips to asserting ValueError before any journal row.
        The name states the property wanted; the assertion records
        what the engine does today.
        """
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-empty-expected"
        path = self.write_log("empty.log", "")
        claim = {"kind": "local-file", "path": path, "expected_text": "", "idempotency_key": "lib-empty-1"}
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, claim, done_clause=DONE_FILE
        )
        self.assertEqual(
            verdict,
            "Correct",
            "PROBE D3: empty expected_text reads Correct (vacuous pass); "
            "the library guard is not yet built, so this records the engine, it does not bless it",
        )
        self.assertEqual(card["verdict"], "Correct")

    def test_crlf_file_with_matching_text(self):
        """H9 probe: a CRLF file whose text matches the LF claim.

        The engine reads the file in text mode with universal
        newlines, so CRLF line endings are translated to LF on read
        and the text matches the claim: Correct. This records that
        behavior. The write_log newline fix is what keeps the test
        files themselves LF on Windows, so the Correct and control
        tests do not depend on the engine's translation.
        """
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-crlf"
        crlf = REDACTED_LOG.replace("\n", "\r\n")
        path = self.write_bytes("crlf.log", crlf.encode("utf-8"))
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-crlf-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Correct", "CRLF is translated on read, so the text matches")
        self.assertEqual(card["verdict"], "Correct")

    def test_journal_records_check_source_and_license(self):
        """Criterion 4: the journal carries each check's source and license.

        Pinned to dedicated columns, not "somewhere in the row": the
        attempts table carries check_source and check_license, and
        BOTH Verify rows for this trace (the call row and the verdict
        row) hold the check's exact source and license there (D5).
        The rows are told apart by reason, not by position:
        verified[0] is the call row, not the verdict row (H3). A
        check_source or check_license that does not exist, or that
        holds the wrong value, fails this test. The implementer adds
        the two columns to the attempts table and stamps them through
        verify_read_back; until then this test fails with a
        missing-column error, which is the test-first signal, not a typo.
        """
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-provenance"
        path = self.write_log("prov.log", RAW_LOG)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-prov-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Incorrect")
        rows = self.rows_for(trace)
        verified = [row for row in rows if row["outcome"] == "verified"]
        self.assertEqual(len(verified), 2, "one call row and one verdict row")
        call_rows = [row for row in verified if row["reason"] == "verify call lib-prov-1"]
        self.assertEqual(len(call_rows), 1, "the call row keeps its reason")
        row = verdict_row(rows, "Incorrect")
        self.assertIsNotNone(row, "the verdict row is found by its reason, not by position")
        self.assertEqual(row["reason"], "verify Incorrect", "the verdict row keeps its reason")
        for target in verified:
            self.assertEqual(target["check_source"], check["source"], "journal must record the check's source")
            self.assertEqual(target["check_license"], check["license"], "journal must record the check's license")
        self.assert_secret_absent(card)

    def test_provenance_follows_the_check_passed_in(self):
        """H5: the stamp follows the check, it is not a hard-coded constant.

        A check built through load_catalog with a different source and
        license must stamp those exact values. A run_check that
        hard-codes the SandHive/MIT stamp passes the old test and
        fails this one.
        """
        self.addCleanup(library_module.load_catalog)
        other = make_check(id="other-source-check", source="Other source", license="Apache-2.0")
        loaded = library_module.load_catalog([other])
        self.assertEqual(len(loaded), 1, "the other check loads")
        check = loaded[0]
        trace = "lib-provenance-other"
        path = self.write_log("other.log", REDACTED_LOG)
        verdict, _ = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-other-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Correct")
        row = verdict_row(self.rows_for(trace), "Correct")
        self.assertIsNotNone(row, "the verdict row is found by its reason")
        self.assertEqual(row["check_source"], "Other source", "the stamp follows the check passed in")
        self.assertEqual(row["check_license"], "Apache-2.0", "the stamp follows the check passed in")

    def test_run_check_refuses_check_without_provenance(self):
        """H5 (D7): run_check refuses a check with no provenance, and journals nothing.

        Criterion 1's "does not load" can be bypassed at run time by
        handing run_check a raw dict, so run_check must refuse it
        itself: None, an empty dict, a check missing its license, and
        a check with a blank source. Nothing is journaled and nothing
        is paused on a refusal.
        """
        path = self.write_log("refuse.log", REDACTED_LOG)
        no_license = make_check(id="refuse-no-license")
        del no_license["license"]
        blank_source = make_check(id="refuse-blank-source", source="   ")
        cases = {"none": None, "empty": {}, "no-license": no_license, "blank-source": blank_source}
        for name, bad in cases.items():
            with self.subTest(case=name):
                trace = f"lib-refuse-{name}"
                claim = self.log_claim(path, f"lib-refuse-{name}-1")
                with self.assertRaises(ValueError):
                    library_module.run_check(self.proxy.state, trace, bad, claim, done_clause=DONE_FILE)
                self.assertEqual(self.rows_for(trace), [], "a refused check journals nothing")
                self.assertFalse(self.proxy.state.is_paused(trace), "a refused check pauses nothing")

    def test_run_check_calls_verify_read_back_once(self):
        """H6: run_check goes through the engine, exactly once, unmutated.

        A run_check that writes equivalent journal rows itself, or
        that calls the engine with a different trace, claim, or done
        clause, fails here. The claim must come back unchanged.
        """
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-spy"
        path = self.write_log("spy.log", REDACTED_LOG)
        claim = self.log_claim(path, "lib-spy-1")
        before = copy.deepcopy(claim)
        with mock.patch.object(
            self.proxy.state, "verify_read_back", wraps=self.proxy.state.verify_read_back
        ) as spy:
            verdict, card = library_module.run_check(
                self.proxy.state, trace, check, claim, done_clause=DONE_FILE
            )
        self.assertEqual(verdict, "Correct")
        spy.assert_called_once()
        args, kwargs = spy.call_args
        got_trace = args[0] if args else kwargs.get("trace_id")
        got_claim = args[1] if len(args) > 1 else kwargs.get("claim")
        got_done = args[2] if len(args) > 2 else kwargs.get("done_clause")
        self.assertEqual(got_trace, trace, "run_check passes the caller's trace")
        self.assertEqual(got_claim, before, "run_check passes the caller's claim unchanged")
        self.assertEqual(got_done, DONE_FILE, "run_check passes the caller's done clause")
        self.assertEqual(claim, before, "run_check must not mutate the claim")
        rows = self.rows_for(trace)
        call_rows = [row for row in rows if row["reason"] == "verify call lib-spy-1"]
        self.assertEqual(len(call_rows), 1, "the call row is journaled with the claim's key")
        row = verdict_row(rows, "Correct")
        self.assertIsNotNone(row, "the verdict row is journaled")
        self.assertEqual(row["reason"], "verify Correct")

    def test_card_keys_are_the_engine_keys(self):
        """N11: the card keeps the engine's keys; the check's source is provenance, not the read.

        The engine card's "source" means what was read ("local
        filesystem"). The check's "source" means provenance
        ("SandHive..."). They are different fields that happen to
        share a name, and the card must not gain a provenance key.
        """
        check = library_module.get_check("secret-redaction-in-logs")
        trace = "lib-card"
        path = self.write_log("card.log", REDACTED_LOG)
        verdict, card = library_module.run_check(
            self.proxy.state, trace, check, self.log_claim(path, "lib-card-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Correct")
        self.assertEqual(
            set(card.keys()),
            {"claim", "what_read", "source", "as_of", "verdict", "why", "next"},
            "the card keeps the engine's keys, with no additions",
        )
        self.assertNotEqual(card["source"], check["source"], "the card's source is what was read, not the check's provenance")
        self.assertEqual(card["source"], "local filesystem")

    def test_existing_journal_gets_check_columns(self):
        """H4: an existing journal gains the check columns by migration.

        CREATE TABLE IF NOT EXISTS does not add columns to an existing
        attempts.db, so an implementer who edits only the DDL goes
        green on a fresh database and breaks every existing journal on
        its first insert. This database is built with the old schema
        and opened through db.init_db, the same init path
        proxy.serve uses, so a missing migration fails here.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = self._old_schema_db(tmp, [("old-row", "allowed")])
        migrated = db_module.init_db(db_path)
        try:
            cols = {row[1] for row in migrated.execute("PRAGMA table_info(attempts)")}
            self.assertIn("check_source", cols, "migration must add check_source")
            self.assertIn("check_license", cols, "migration must add check_license")
            row = migrated.execute(
                "SELECT check_source, check_license FROM attempts WHERE trace_id = 'old-row'"
            ).fetchone()
            self.assertIsNotNone(row, "the old row must still be readable")
            self.assertEqual(row[0], "", "an old row has empty provenance")
            self.assertEqual(row[1], "", "an old row has empty provenance")
        finally:
            migrated.close()

    def test_non_library_rows_have_empty_provenance(self):
        """H4: rows not written by the library carry empty provenance.

        A traffic row, and both Verify rows from a direct engine
        Verify with no library, have check_source and check_license
        equal to "". This matches the table's TEXT NOT NULL DEFAULT ''
        convention. Until the columns exist this fails with a
        missing-column error, which is the test-first signal.
        """
        trace = "lib-nonlib-traffic"
        sock, status = self.open_tunnel(self.dest, trace)
        try:
            self.assertEqual(status, 200, "the traffic tunnel must flow")
            sock.sendall(b"12345")
            self.assertEqual(sock.recv(65536), b"12345")
        finally:
            sock.close()
        self.wait_for_rows(trace)
        for row in self.rows_for(trace):
            self.assertEqual(row["check_source"], "", "a traffic row has no check source")
            self.assertEqual(row["check_license"], "", "a traffic row has no check license")
        trace = "lib-nonlib-verify"
        path = self.write_log("nonlib.log", REDACTED_LOG)
        verdict, _ = self.proxy.state.verify_read_back(
            trace, self.log_claim(path, "lib-nonlib-1"), done_clause=DONE_FILE
        )
        self.assertEqual(verdict, "Correct")
        verified = [row for row in self.rows_for(trace) if row["outcome"] == "verified"]
        self.assertEqual(len(verified), 2, "one call row and one verdict row")
        for row in verified:
            self.assertEqual(row["check_source"], "", "a direct Verify has no check source")
            self.assertEqual(row["check_license"], "", "a direct Verify has no check license")

    def test_outcome_accounting_unchanged_by_new_columns(self):
        """H11: the new columns do not change outcome accounting.

        db.fetch_outcome_counts sums the journal by outcome. The
        counts on an old-schema database must be identical before
        and after the migration opens it, so the new columns add
        provenance, not a recount.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rows = [
            ("count-0", "allowed"),
            ("count-1", "allowed"),
            ("count-2", "blocked"),
            ("count-3", "paused"),
            ("count-4", "verified"),
            ("count-5", ""),
        ]
        db_path = self._old_schema_db(tmp, rows)
        conn = sqlite3.connect(db_path)
        try:
            before = db_module.fetch_outcome_counts(conn)
        finally:
            conn.close()
        migrated = db_module.init_db(db_path)
        try:
            after = db_module.fetch_outcome_counts(migrated)
        finally:
            migrated.close()
        self.assertEqual(after, before, "outcome counts must be identical before and after migration")
        self.assertEqual(before["allowed"], 2)
        self.assertEqual(before["blocked"], 1)
        self.assertEqual(before["paused"], 1)
        self.assertEqual(before["verified"], 1)
        self.assertEqual(before["other"], 1, "an empty outcome counts as other")


if __name__ == "__main__":
    unittest.main()
