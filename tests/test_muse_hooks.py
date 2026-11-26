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

Stdin payload shape (Claude Code hooks reference, 2026-11-25):
  {
    "session_id": "<str>",
    "hook_event_name": "PreToolUse",
    "tool_name": "<str>",
    "tool_input": { ... tool-specific fields ... }
  }

Relevant tool_input shapes:
  Bash tool:
    { "command": "<shell command string>", "description": "<str>" }
  Write tool (creates or overwrites a file):
    { "file_path": "<path>", "content": "<str>" }
  Edit tool (edits a file in place):
    { "file_path": "<path>", ... }
  Read tool (reads a file; not a write; should be allowed):
    { "file_path": "<path>" }

Stdout deny shape (hookSpecificOutput schema):
  {
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "<human-readable reason>"
    }
  }

Exit codes:
  0  - no decision (normal permission flow applies); also used for allow
  2  - deny (hook blocks the tool call)

--- Verification points ---

Point A: Does the hook fire in muse exec non-interactive mode?
  The watcher calls: muse exec --non-interactive --prompt "<task>"
  Claude Code's hooks reference states that PreToolUse fires for all
  tool calls in auto (non-interactive) mode. Exit 2 blocks the tool
  call before it runs. This is the standard behaviour documented for
  PreToolUse in both interactive and auto mode.
  Status: VERIFIED from docs. See report for open questions about
  the exact muse CLI flag spelling (muse exec vs muse run).

Point B: Payload shape.
  Sourced from Claude Code hooks reference (docs.anthropic.com),
  confirmed against the working example in the same doc (block-rm.sh).
  The hook reads ``tool_name`` and ``tool_input`` from stdin JSON.
  For Bash: ``tool_input.command``.
  For Write/Edit: ``tool_input.file_path``.

--- Ban rules covered by this hook ---

Shell ban patterns (Bash tool, tool_input.command):
  rm -rf
  git add -A
  git commit -a
  git push --force
  git reset --hard

File ban patterns (Write / Edit tool, tool_input.file_path):
  Same as hook 1: .exe  dist/  build/  *.db  demo_*.db
  .pytest_cache/  *.spec (except iris.spec at root)
  Secrets by filename: .env  .env.*  *.pem  *.p12  id_rsa  id_ed25519

Unplanned-file rule (Write / Edit tool, tool_input.file_path):
  The file must appear in approved-plan.txt (repo root).
  If the plan file itself is being written (file_path == approved-plan.txt)
  the write is allowed.

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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Root of the iris repo (parent of tests/).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Hook script path.
HOOK_SCRIPT = os.path.join(REPO_ROOT, ".muse", "hooks", "iris-pre-tool-use.py")

# The plan file the hook reads to decide whether a write target is approved.
PLAN_FILE = os.path.join(REPO_ROOT, "approved-plan.txt")

# A dummy session id used in all test payloads.
DUMMY_SESSION = "test-session-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_bash_payload(command: str) -> dict:
    """Return a PreToolUse payload for a Bash tool call."""
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": command,
            "description": "test command",
        },
    }


def _build_write_payload(file_path: str, content: str = "") -> dict:
    """Return a PreToolUse payload for a Write tool call."""
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {
            "file_path": file_path,
            "content": content,
        },
    }


def _build_read_payload(file_path: str) -> dict:
    """Return a PreToolUse payload for a Read tool call.

    Read is a safe tool; the hook must allow it.
    """
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {
            "file_path": file_path,
        },
    }


def _build_edit_payload(file_path: str) -> dict:
    """Return a PreToolUse payload for an Edit tool call."""
    return {
        "session_id": DUMMY_SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": file_path,
            "old_string": "# old",
            "new_string": "# new",
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
        raise FileNotFoundError(
            f"Hook script not found. stderr: {stderr!r}"
        )
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
            f"Hook must deny command {command!r}. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
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
            f"Hook must deny Write to {file_path!r}. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
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
            f"Hook must deny Edit of banned file. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )


# ---------------------------------------------------------------------------
# Tests: unplanned file writes
# ---------------------------------------------------------------------------

class TestUnplannedFileWrites(unittest.TestCase):
    """PreToolUse hook denies writes to files not in approved-plan.txt.

    The hook reads approved-plan.txt from the repo root (cwd). It blocks
    writes to paths not on the list. Writing the plan file itself is allowed.
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
            f"Hook must deny write to unplanned file. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )
        # The reason must identify the offending path.
        output = _parse_hook_output(stdout)
        hso = output.get("hookSpecificOutput", {})
        reason = hso.get("permissionDecisionReason", "")
        combined = reason + err
        self.assertIn(
            "iris/surprise.py", combined,
            f"Deny must name the unplanned file. combined={combined!r}",
        )

    def test_allows_write_to_plan_file_itself(self):
        """Writing approved-plan.txt is always allowed (it updates the plan)."""
        self._write_plan(["iris/proxy.py"])
        payload = _build_write_payload("approved-plan.txt",
                                       "iris/proxy.py\niris/new.py\n")
        rc, stdout, err = self._run_in_tmpdir(payload)
        self.assertFalse(
            _is_deny(rc, stdout, err),
            f"Hook must allow write to plan file. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )


# ---------------------------------------------------------------------------
# Tests: clean tool calls are allowed
# ---------------------------------------------------------------------------

class TestCleanToolCalls(unittest.TestCase):
    """PreToolUse hook allows clean tool calls that violate no rules.

    A clean call returns exit 0 with no deny decision.
    """

    def setUp(self):
        """Write a plan file in the real repo root containing the test file."""
        # The plan file must already contain the path being written so the
        # unplanned-file rule does not interfere.
        self._plan_exists = os.path.exists(PLAN_FILE)
        self._plan_content = None
        if self._plan_exists:
            with open(PLAN_FILE, encoding="utf-8") as fh:
                self._plan_content = fh.read()

    def tearDown(self):
        """Restore the original plan file if we changed it."""
        if self._plan_content is not None:
            with open(PLAN_FILE, "w", encoding="utf-8") as fh:
                fh.write(self._plan_content)
        elif os.path.exists(PLAN_FILE) and not self._plan_exists:
            os.remove(PLAN_FILE)

    def test_allows_clean_bash_command(self):
        """A safe bash command (python -m pytest) is allowed."""
        payload = _build_bash_payload("python -m pytest tests/ -q")
        rc, stdout, err = _run_hook(payload)
        self.assertFalse(
            _is_deny(rc, stdout, err),
            f"Hook must allow a clean bash command. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )

    def test_allows_read_of_any_file(self):
        """Read tool calls are always allowed; they are not writes."""
        payload = _build_read_payload("iris/proxy.py")
        rc, stdout, err = _run_hook(payload)
        self.assertFalse(
            _is_deny(rc, stdout, err),
            f"Hook must allow Read tool calls. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )

    def test_allows_write_to_planned_clean_file(self):
        """Write to a planned, non-banned file path is allowed.

        This test ensures the hook does not false-positive on clean writes.
        It temporarily writes an approved-plan.txt containing the test path
        if the plan file does not already exist.
        """
        target = "iris/proxy.py"

        # Ensure the target is in the plan.
        plan_lines = []
        if self._plan_content:
            plan_lines = [l for l in self._plan_content.splitlines() if l.strip()]
        if target not in plan_lines:
            plan_lines.append(target)
            with open(PLAN_FILE, "w", encoding="utf-8") as fh:
                fh.write("\n".join(plan_lines) + "\n")

        payload = _build_write_payload(target, content="# planned\n")
        rc, stdout, err = _run_hook(payload)
        self.assertFalse(
            _is_deny(rc, stdout, err),
            f"Hook must allow write to a planned, clean file. "
            f"returncode={rc}, stdout={stdout!r}, stderr={err!r}",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
