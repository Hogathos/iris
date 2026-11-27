"""Muse Code PreToolUse hook tests: iris-pre-tool-use.py.

Hook 4 of the hooks-as-deterministic-gates plan. The hook script lives at:
  .muse/hooks/iris-pre-tool-use.py

It is wired to the PreToolUse event via .muse/hooks.json (see the config
section below for the exact format). It is a plain stdlib-only Python script
with no external dependencies.

--- How the hook runs ---

Muse Code calls the hook before every tool call. The hook reads a JSON
payload from stdin, checks the tool name and arguments against the ban rules,
and writes its decision to stdout as JSON. It signals a deny via exit code 2
(the standard Claude Code / Muse Code deny exit code).

Stdin payload shape (verified against Muse Code 1.4.25 live PreToolUse hook):
  {
    "session_id": "<str>",
    "hook_event_name": "PreToolUse",
    "tool_name": "<str>",
    "tool_input": { ... tool-specific fields ... }
  }

Relevant tool_input shapes in Muse Code:
  powershell / shell tool:
    { "command": "<shell command string>", "workdir": "<str>" }
  write_file tool (creates or overwrites a file):
    { "path": "<path>", "content": "<str>" }
  edit_file tool (edits a file in place):
    { "path": "<path>", "find": "<str>", "replace": "<str>" }
  read_file tool (reads a file; not a write; should be allowed):
    { "path": "<path>" }

Stdout deny shape (hookSpecificOutput schema):
  {
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "<human-readable reason>"
    }
  }

Exit codes:
  0  - no decision (empty stdout; bare allow without updatedInput is rejected)
  2  - deny (hook blocks the tool call; stderr/reason reported to model)

--- Verification points ---

Point A: Does the hook fire in muse exec non-interactive mode?
  The watcher calls: muse exec --prompt-file <path> --disable-approval --trust-workspace
  Verified via live loopback muse exec run against Muse Code 1.4.25: PreToolUse
  fires even with --disable-approval --disable-sandbox --trust-workspace
  (permission_mode="bypassPermissions") and exit 2 blocks the tool call before
  execution.

Point B: Payload shape.
  Verified against Muse Code 1.4.25 binary and live PreToolUse stdin capture:
  Muse sends tool_name ("powershell", "write_file", "edit_file", "read_file")
  and tool_input.command / tool_input.path (differs from Claude Code's
  Bash/Write/Edit/Read and tool_input.file_path).

--- Ban rules covered by this hook ---

Shell ban patterns (powershell tool, tool_input.command):
  rm -rf
  git add -A
  git commit -a
  git push --force
  git reset --hard

File ban patterns (write_file / edit_file tool, tool_input.path):
  Same as hook 1: .exe  dist/  build/  *.db  demo_*.db
  .pytest_cache/  *.spec (except iris.spec at root)
  Secrets by filename: .env  .env.*  *.pem  *.p12  id_rsa  id_ed25519

Unplanned-file rule:
  The target must appear in approved-plan.txt at the repository root.
  Only a human can change the plan.

--- Test strategy ---

Each test builds a minimal payload dict, writes it to a temporary file, then
calls the hook script as a subprocess with stdin redirected from that file.
The hook is called with the same Python interpreter running the tests.
Tests do NOT require a live Muse Code session.

The hook script path is:
  <repo_root>/.muse/hooks/iris-pre-tool-use.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Root of the iris repo (parent of tests/).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Hook script path.
HOOK_SCRIPT = os.path.join(REPO_ROOT, ".muse", "hooks", "iris-pre-tool-use.py")

# A dummy session id used in all test payloads.
DUMMY_SESSION = "test-session-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_bash_payload(command: str) -> dict:
    """Return a PreToolUse payload for a Muse shell (powershell) tool call."""
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "powershell",
        "tool_input": {
            "command": command,
            "description": "test command",
        },
    }


def _build_write_payload(file_path: str, content: str = "") -> dict:
    """Return a PreToolUse payload for a Muse write_file tool call."""
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "write_file",
        "tool_input": {
            "path": file_path,
            "content": content,
        },
    }


def _build_read_payload(file_path: str) -> dict:
    """Return a PreToolUse payload for a Muse read_file tool call.

    read_file is a safe tool; the hook must allow it.
    """
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "read_file",
        "tool_input": {
            "path": file_path,
        },
    }


def _build_edit_payload(file_path: str) -> dict:
    """Return a PreToolUse payload for a Muse edit_file tool call."""
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "edit_file",
        "tool_input": {
            "path": file_path,
            "find": "# old",
            "replace": "# new",
        },
    }


def _run_hook(payload: dict, repo_root: str = None) -> tuple:
    """Run the hook script with the given payload on stdin.

    Returns (returncode, stdout_text, stderr_text).
    The hook is called with the Python interpreter running these tests.
    The cwd is set to repo_root so the hook can find approved-plan.txt.
    """
    cwd = repo_root or REPO_ROOT
    payload_bytes = json.dumps(payload).encode()
    result = subprocess.run(
        [sys.executable, HOOK_SCRIPT],
        input=payload_bytes,
        capture_output=True,
        cwd=cwd,
    )
    return (
        result.returncode,
        result.stdout.decode(errors="replace"),
        result.stderr.decode(errors="replace"),
    )


def _parse_hook_output(stdout: str) -> dict:
    """Parse the hook's stdout JSON. Return {} on empty or invalid JSON.

    The hook outputs a JSON object when it has a decision. If it exits 0
    with no output, the caller should check the exit code directly.
    """
    stdout = stdout.strip()
    if not stdout:
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {}


def _is_deny(returncode: int, stdout: str, stderr: str = "") -> bool:
    """Return True if the hook signalled a deny.

    A deny is either:
    - Exit code 2  (the canonical deny exit code per Claude Code docs), or
    - Exit code 0 with hookSpecificOutput.permissionDecision == "deny".

    The hook may use either mechanism. This helper accepts both.

    Guard: Python itself exits with code 2 when it cannot open the script
    file ("can't open file ... No such file or directory"). That is not a
    deny; it is a missing-script error. If stderr carries that message,
    raise FileNotFoundError so the test fails clearly rather than passing
    for the wrong reason.
    """
    if returncode == 2 and "No such file or directory" in stderr:
        raise FileNotFoundError(f"Hook script not found. stderr: {stderr!r}")
    if returncode == 2:
        return True
    if returncode == 0:
        output = _parse_hook_output(stdout)
        hso = output.get("hookSpecificOutput", {})
        return hso.get("permissionDecision") == "deny"
    return False


# ---------------------------------------------------------------------------
# Tests: shell command bans
# ---------------------------------------------------------------------------


class TestShellCommandBans(unittest.TestCase):
    """PreToolUse hook denies banned shell commands before they run.

    Ban patterns:
      rm -rf
      git add -A
      git commit -a
      git push --force
      git reset --hard
    """

    def _assert_denied(self, command: str):
        """Assert the hook denies the given command. Check the reason too."""
        payload = _build_bash_payload(command)
        rc, stdout, err = _run_hook(payload)
        self.assertTrue(
            _is_deny(rc, stdout, err),
            f"Hook must deny command {command!r}. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )
        # The deny reason must name the blocked pattern.
        output = _parse_hook_output(stdout)
        hso = output.get("hookSpecificOutput", {})
        reason = hso.get("permissionDecisionReason", "")
        # Reason may be in the JSON output or in stderr. Accept either.
        self.assertTrue(
            reason or err,
            f"Deny must carry a reason. stdout={stdout!r}, stderr={err!r}",
        )

    def test_denies_rm_rf(self):
        """rm -rf is denied before it runs."""
        self._assert_denied("rm -rf /some/path")

    def test_denies_git_add_A(self):
        """git add -A is denied before it runs."""
        self._assert_denied("git add -A")

    def test_denies_git_commit_a(self):
        """git commit -a is denied before it runs."""
        self._assert_denied("git commit -a -m 'message'")

    def test_denies_git_push_force(self):
        """git push --force is denied before it runs."""
        self._assert_denied("git push --force origin main")

    def test_denies_git_reset_hard(self):
        """git reset --hard is denied before it runs."""
        self._assert_denied("git reset --hard HEAD~1")

    def test_shell_denial_omits_user_arguments(self):
        marker = "private-argument-marker"
        rc, stdout, err = _run_hook(_build_bash_payload(f"git push --force {marker}"))
        self.assertTrue(_is_deny(rc, stdout, err))
        self.assertNotIn(marker, stdout + err)

    def test_commit_message_can_describe_an_option(self):
        rc, stdout, err = _run_hook(_build_bash_payload('git commit -m "document the -a option"'))
        self.assertEqual((rc, stdout, err), (0, "", ""))


# ---------------------------------------------------------------------------
# Tests: banned file writes
# ---------------------------------------------------------------------------


class TestBannedFileWrites(unittest.TestCase):
    """PreToolUse hook denies writes to banned file types before they run.

    The same file ban list as hook 1 applies.
    """

    def _assert_write_denied(self, file_path: str):
        """Assert the hook denies a Write to file_path."""
        payload = _build_write_payload(file_path, content="data\n")
        rc, stdout, err = _run_hook(payload)
        self.assertTrue(
            _is_deny(rc, stdout, err),
            f"Hook must deny Write to {file_path!r}. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )

    def test_denies_write_exe(self):
        """Write to a .exe path is denied."""
        self._assert_write_denied("dist/mytool.exe")

    def test_denies_write_db(self):
        """Write to a *.db path is denied."""
        self._assert_write_denied("attempts.db")

    def test_denies_write_secret_env(self):
        """Write to .env is denied."""
        self._assert_write_denied(".env")

    def test_denies_write_secret_pem(self):
        """Write to *.pem is denied."""
        self._assert_write_denied("certs/server.pem")

    def test_denies_write_spec(self):
        """Write to a *.spec file (not iris.spec) is denied."""
        self._assert_write_denied("build.spec")

    def test_denies_edit_banned_file(self):
        """Edit of a banned file type is also denied."""
        payload = _build_edit_payload("attempts.db")
        rc, stdout, err = _run_hook(payload)
        self.assertTrue(
            _is_deny(rc, stdout, err),
            f"Hook must deny Edit of banned file. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )


# ---------------------------------------------------------------------------
# Tests: unplanned file writes
# ---------------------------------------------------------------------------


class TestUnplannedFileWrites(unittest.TestCase):
    """PreToolUse hook denies writes to files not in approved-plan.txt.

    The hook reads approved-plan.txt from the repo root. It blocks writes
    to paths outside the list and to the plan itself.
    """

    def setUp(self):
        """Create a temp directory that acts as the repo root for each test.

        The hook's cwd is set to this directory, so it reads plan from there.
        """
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_plan(self, approved_files: list[str]) -> None:
        """Write approved-plan.txt into the temp repo root."""
        plan_path = os.path.join(self.tmpdir, "approved-plan.txt")
        with open(plan_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(approved_files) + "\n")

    def _run_in_tmpdir(self, payload: dict) -> tuple:
        """Run the hook with cwd = tmpdir so it reads plan from there."""
        return _run_hook(payload, repo_root=self.tmpdir)

    def test_denies_write_to_unplanned_file(self):
        """Write to a file not in approved-plan.txt is denied.

        The deny reason must name the unplanned file.
        """
        self._write_plan(["iris/proxy.py", "README.md"])
        payload = _build_write_payload("iris/surprise.py", "# oops\n")
        rc, stdout, err = self._run_in_tmpdir(payload)
        self.assertTrue(
            _is_deny(rc, stdout, err),
            f"Hook must deny write to unplanned file. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )
        # The reason must identify the offending path.
        output = _parse_hook_output(stdout)
        hso = output.get("hookSpecificOutput", {})
        reason = hso.get("permissionDecisionReason", "")
        combined = reason + err
        self.assertIn(
            "iris/surprise.py",
            combined,
            f"Deny must name the unplanned file. combined={combined!r}",
        )

    def test_denies_write_to_plan_file_itself(self):
        """The human owns changes to approved-plan.txt."""
        self._write_plan(["iris/proxy.py"])
        payload = _build_write_payload("approved-plan.txt", "iris/proxy.py\niris/new.py\n")
        rc, stdout, err = self._run_in_tmpdir(payload)
        self.assertTrue(
            _is_deny(rc, stdout, err),
            f"Hook must deny writing the plan file. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )

    def test_missing_plan_denies_normal_write(self):
        self.assertTrue(_is_deny(*self._run_in_tmpdir(_build_write_payload("notes.md"))))

    def test_unreadable_plan_denies_normal_write(self):
        Path(self.tmpdir, "approved-plan.txt").write_bytes(b"\xff")
        self.assertTrue(_is_deny(*self._run_in_tmpdir(_build_write_payload("notes.md"))))

    def test_plan_with_bom_allows_listed_file(self):
        for encoding in ("utf-8-sig", "utf-16"):
            with self.subTest(encoding=encoding):
                Path(self.tmpdir, "approved-plan.txt").write_text("notes.md\n", encoding=encoding)
                self.assertEqual(self._run_in_tmpdir(_build_write_payload("notes.md")), (0, "", ""))

    def test_subdirectory_uses_root_plan(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmpdir, check=True, capture_output=True)
        Path(self.tmpdir, "docs").mkdir()
        self._write_plan(["docs/notes.md"])
        payload = _build_write_payload("notes.md")
        payload["cwd"] = str(Path(self.tmpdir, "docs"))
        self.assertEqual(self._run_in_tmpdir(payload), (0, "", ""))
        payload["tool_input"]["path"] = "draft.md"
        self.assertTrue(_is_deny(*self._run_in_tmpdir(payload)))

    def test_supported_write_shapes_require_an_approved_target(self):
        self._write_plan(["notes.md", "lab.ipynb"])
        for tool, tool_input in (
            ("apply_patch", {"input": "*** Add File: notes.md\n+Notes"}),
            ("NotebookEdit", {"notebook_path": "lab.ipynb"}),
            ("mcp__fs__write_file", {"filename": "notes.md"}),
        ):
            with self.subTest(tool=tool):
                payload = {"tool_name": tool, "tool_input": tool_input}
                self.assertEqual(self._run_in_tmpdir(payload), (0, "", ""))
                bad_input = {key: None for key in tool_input}
                self.assertTrue(_is_deny(*self._run_in_tmpdir({**payload, "tool_input": bad_input})))


# ---------------------------------------------------------------------------
# Tests: clean tool calls are allowed
# ---------------------------------------------------------------------------


class TestCleanToolCalls(unittest.TestCase):
    """PreToolUse hook allows clean tool calls that violate no rules.

    A clean call returns exit 0 with no deny decision.
    """

    def setUp(self):
        """Create a temp directory that acts as the repo root for each test."""
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        """Remove the temporary directory created for the test."""
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_allows_clean_bash_command(self):
        """A safe bash command (python -m pytest) is allowed."""
        payload = _build_bash_payload("python -m pytest tests/ -q")
        rc, stdout, err = _run_hook(payload, repo_root=self.tmpdir)
        self.assertFalse(
            _is_deny(rc, stdout, err),
            f"Hook must allow a clean bash command. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )

    def test_allows_read_of_any_file(self):
        """Read tool calls are always allowed; they are not writes."""
        payload = _build_read_payload("iris/proxy.py")
        rc, stdout, err = _run_hook(payload, repo_root=self.tmpdir)
        self.assertFalse(
            _is_deny(rc, stdout, err),
            f"Hook must allow Read tool calls. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )

    def test_allows_write_to_planned_clean_file(self):
        """Write to a planned, non-banned file path is allowed.

        This test ensures the hook does not false-positive on clean writes.
        It writes an approved-plan.txt containing the test path inside the
        temporary repo root.
        """
        target = "iris/proxy.py"

        plan_path = os.path.join(self.tmpdir, "approved-plan.txt")
        with open(plan_path, "w", encoding="utf-8") as fh:
            fh.write(target + "\n")

        payload = _build_write_payload(target, content="# planned\n")
        rc, stdout, err = _run_hook(payload, repo_root=self.tmpdir)
        self.assertFalse(
            _is_deny(rc, stdout, err),
            f"Hook must allow write to a planned, clean file. returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
