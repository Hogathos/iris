"""Pre-commit hook tests: banned files, fast lint+types, unplanned files.

Three hooks live in .git/hooks/ (or hooks/ for install):
  - iris-check-banned-files  (hook 1)
  - iris-check-lint          (hook 2)
  - iris-check-plan          (hook 3)

Each hook is a plain Python script. It reads the staged file list from
``git diff --cached --name-only``, applies its rule, and exits non-zero
on failure. The exit message goes to stderr.

Test strategy: each test sets up a temporary git repository, stages
specific files, then calls the hook script as a subprocess. The hook
must not exist yet for the tests to fail on their own (they import-fail
or file-not-found on the script path).

The hook scripts will live at:
  hooks/iris-check-banned-files.py
  hooks/iris-check-lint.py
  hooks/iris-check-plan.py

An installer (hooks/install.py or a Makefile target) copies them into
.git/hooks/ of the working tree.

Plan file convention: the unplanned-files hook reads the approved plan
from a file at the repo root called ``approved-plan.txt``. Each line
names one approved file path (relative to repo root). The hook blocks
any staged file not on that list, unless the plan file itself is staged.

Non-goals for this test file:
- Full pytest suite correctness (that stays in CI).
- Windows-only quirks (CI covers both platforms).
- Edge cases inside ruff or pyright (we trust those tools).

Verification points pinned here (see report for how they were sourced):
- Hook scripts are called with no arguments; they query git themselves.
- Exit code != 0 means block; stderr carries the reason.
- The plan-file escape hatch: staging the plan file suppresses the block.
"""

import json
import os
import runpy
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Root of the iris repo (parent of tests/).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Hook script paths (relative to repo root).
HOOK_BANNED = os.path.join(REPO_ROOT, "hooks", "iris-check-banned-files.py")
HOOK_LINT = os.path.join(REPO_ROOT, "hooks", "iris-check-lint.py")
HOOK_PLAN = os.path.join(REPO_ROOT, "hooks", "iris-check-plan.py")
INSTALLER = os.path.join(REPO_ROOT, "hooks", "install.py")

# The plan file name the unplanned-files hook reads.
PLAN_FILE = "approved-plan.txt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(args, cwd, check=True, capture=True):
    """Run a git command inside cwd. Return CompletedProcess."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=check,
    )


def _make_temp_repo():
    """Create a temporary git repo. Return (tmpdir path, cleanup callable).

    The caller must call cleanup() when done.
    """
    tmpdir = tempfile.mkdtemp()

    # Minimal git identity so commits don't fail.
    _git(["init", "-b", "main"], cwd=tmpdir)
    _git(["config", "user.email", "test@example.com"], cwd=tmpdir)
    _git(["config", "user.name", "Test"], cwd=tmpdir)

    def cleanup():
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    return tmpdir, cleanup


def _stage_file(repo, rel_path, content="placeholder\n"):
    """Write content to rel_path inside repo and git-add it.

    Parent directories are created as needed.
    """
    abs_path = os.path.join(repo, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    _git(["add", rel_path], cwd=repo)


def _run_hook(hook_script, repo):
    """Run hook_script as a subprocess inside repo.

    Return (returncode, stdout, stderr).
    The hook is called with the same Python interpreter running the tests.
    """
    result = subprocess.run(
        [sys.executable, hook_script],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Hook 1: banned files
# ---------------------------------------------------------------------------


class TestBannedFilesHook(unittest.TestCase):
    """Hook 1: iris-check-banned-files.py

    Banned patterns from AGENTS.md:
      .exe  dist/  build/  *.db  demo_*.db  .pytest_cache/  *.spec  secrets

    One exception: iris.spec at repo root is already tracked and is allowed.

    Secret filename patterns (representative sample):
      .env  .env.*  *_secret*  *_key*  *.pem  *.p12  id_rsa  id_ed25519
    These are pinned in the hook; this test covers a representative subset.
    """

    def setUp(self):
        self.repo, self.cleanup = _make_temp_repo()

    def tearDown(self):
        self.cleanup()

    # -- Fail cases: hook must block and name the file ---------------------

    def test_blocks_exe_file(self):
        """A staged .exe file is blocked. The block names the file."""
        _stage_file(self.repo, "mytool.exe")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block a staged .exe file")
        self.assertIn("mytool.exe", err, "Block message must name the offending file")

    def test_blocks_db_file(self):
        """A staged *.db file is blocked. The block names the file."""
        _stage_file(self.repo, "attempts.db")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block a staged .db file")
        self.assertIn("attempts.db", err, "Block message must name the offending file")

    def test_blocks_demo_db_file(self):
        """A staged demo_*.db file is blocked."""
        _stage_file(self.repo, "demo_foo.db")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block a staged demo_*.db file")
        self.assertIn("demo_foo.db", err, "Block message must name the offending file")

    def test_blocks_secret_by_filename_env(self):
        """A staged .env file (secret by filename) is blocked."""
        _stage_file(self.repo, ".env")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block a staged .env file")
        self.assertIn(".env", err, "Block message must name the offending file")

    def test_blocks_secret_by_filename_pem(self):
        """A staged *.pem file (secret by filename) is blocked."""
        _stage_file(self.repo, "server.pem")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block a staged .pem file")
        self.assertIn("server.pem", err, "Block message must name the offending file")

    def test_blocks_spec_file(self):
        """A staged *.spec file (not iris.spec) is blocked."""
        _stage_file(self.repo, "build.spec")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block a staged .spec file")
        self.assertIn("build.spec", err, "Block message must name the offending file")

    # -- Pass cases: hook must allow clean commits -------------------------

    def test_passes_clean_files(self):
        """A commit staging only clean .py and .md files passes."""
        _stage_file(self.repo, "iris/proxy.py", "# clean\n")
        _stage_file(self.repo, "README.md", "# readme\n")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertEqual(rc, 0, f"Hook should pass clean files. stderr: {err!r}")

    def test_allows_iris_spec_exception(self):
        """The existing iris.spec at repo root is the one allowed exception.

        The hook must not block it. This test stages iris.spec alone and
        expects exit 0.

        Implementation note: the hook checks whether the file is tracked
        already in the real repo AND sits at the root with the exact name
        iris.spec. In a fresh temp repo it is not pre-tracked, so the hook
        may still allow it by name pattern alone. The test just asserts exit 0
        to cover the exception path.
        """
        _stage_file(self.repo, "iris.spec", "# -*- mode: python -*- # hand-written spec\n")
        rc, _out, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertEqual(rc, 0, f"iris.spec at root must be allowed. stderr: {err!r}")


# ---------------------------------------------------------------------------
# Hook 2: fast lint + types
# ---------------------------------------------------------------------------


class TestFileNames(unittest.TestCase):
    def setUp(self):
        self.repo, cleanup = _make_temp_repo()
        self.addCleanup(cleanup)

    def test_allows_source_and_example_names(self):
        for path in ("tests/test_api_key_parser.py", "tests/test_secrets.py", "src/api_key_utils.py", ".env.example"):
            _stage_file(self.repo, path)
        self.assertEqual(_run_hook(HOOK_BANNED, self.repo), (0, "", ""))

    def test_blocks_credentials_and_mixed_case_directories(self):
        paths = ("secrets.json", "secret.txt", "credentials.json", "token.txt", "BUILD/x.py", "Dist/a.txt")
        for path in paths:
            _stage_file(self.repo, path)
        rc, _, err = _run_hook(HOOK_BANNED, self.repo)
        self.assertEqual(rc, 1)
        for path in paths:
            self.assertIn(path, err)


class TestLintHook(unittest.TestCase):
    """Hook 2: iris-check-lint.py

    Runs three tools against the staged Python files:
      ruff check .
      ruff format --check .
      pyright

    The hook blocks the commit if any tool exits non-zero. It prints the
    failure name (and the tool's output) to stderr.

    Test strategy: write a Python file with a known ruff error or format
    violation, stage it, and confirm the hook blocks. Then write a clean
    file and confirm the hook passes.

    Known ruff parse-error trigger (E999 / syntax error):
      A file with a bare ``raise`` and a following statement on the same
      line produces F-series errors.

    Known ruff format violation:
      Extra blank lines, wrong indentation style, trailing whitespace.
    """

    def setUp(self):
        self.repo, self.cleanup = _make_temp_repo()

    def tearDown(self):
        self.cleanup()

    # -- Fail cases -------------------------------------------------------

    def test_blocks_ruff_check_failure(self):
        """ruff check fails on an undefined name. The hook blocks and names it.

        F821: undefined name. This is a hard-error class ruff always catches.
        """
        bad_py = textwrap.dedent("""\
            # This file has an undefined name.
            x = undefined_name_xyz
        """)
        _stage_file(self.repo, "broken.py", bad_py)
        rc, _out, err = _run_hook(HOOK_LINT, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block on ruff check failure")
        # The hook must name which tool failed.
        self.assertIn("ruff", err.lower(), "Block message must mention ruff. stderr: " + err)

    def test_blocks_ruff_format_failure(self):
        """ruff format --check fails on badly formatted code. The hook blocks.

        A file with inconsistent spacing will fail ruff format --check.
        """
        bad_format = textwrap.dedent("""\
            def foo( ):
                x=1
                y =   2
                return x+y
        """)
        _stage_file(self.repo, "ugly.py", bad_format)
        rc, _out, err = _run_hook(HOOK_LINT, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block on ruff format --check failure")
        self.assertIn("ruff", err.lower(), "Block message must mention ruff. stderr: " + err)

    def test_blocks_pyright_failure(self):
        """pyright fails on a type error. The hook blocks and names pyright.

        A simple assignment of a string to an int-typed variable is a
        type error pyright reliably catches.
        """
        bad_types = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b


            result: int = add("hello", 1)  # type error: str vs int
        """)
        _stage_file(self.repo, "typed_bad.py", bad_types)
        rc, _out, err = _run_hook(HOOK_LINT, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block on pyright type failure")
        self.assertIn("pyright", err.lower(), "Block message must mention pyright. stderr: " + err)

    # -- Pass case --------------------------------------------------------

    def test_passes_clean_python(self):
        """A clean, well-typed Python file passes all three tools."""
        clean_py = textwrap.dedent("""\
            \"\"\"Clean module.\"\"\"


            def add(a: int, b: int) -> int:
                \"\"\"Return the sum of a and b.\"\"\"
                return a + b
        """)
        _stage_file(self.repo, "clean.py", clean_py)
        rc, _out, err = _run_hook(HOOK_LINT, self.repo)
        self.assertEqual(rc, 0, f"Hook should pass clean Python. stderr: {err!r}")

    def test_checks_index_even_when_working_file_differs(self):
        _stage_file(self.repo, "draft.py", "x = 'wrong'\n")
        Path(self.repo, "draft.py").write_text('x = "clean"\n', encoding="utf-8")
        rc, _, err = _run_hook(HOOK_LINT, self.repo)
        self.assertEqual(rc, 1)
        self.assertIn("ruff format --check failed", err)
        self.assertEqual(Path(self.repo, "draft.py").read_text(encoding="utf-8"), 'x = "clean"\n')

    def test_ruff_honors_staged_excludes(self):
        _stage_file(self.repo, "ruff.toml", 'extend-exclude = ["scratch.py"]\n')
        _stage_file(self.repo, "scratch.py", "x=1\n")
        self.assertEqual(_run_hook(HOOK_LINT, self.repo), (0, "", ""))


# ---------------------------------------------------------------------------
# Hook 3: unplanned files
# ---------------------------------------------------------------------------


class TestUnplannedFilesHook(unittest.TestCase):
    """Hook 3: iris-check-plan.py

    The hook reads approved-plan.txt (repo root). Each line is an approved
    file path. A staged file not on the list is blocked, unless the plan
    file itself is staged (updating the plan covers the new files).

    The hook must name the unplanned file in the block message.
    """

    def setUp(self):
        self.repo, self.cleanup = _make_temp_repo()

    def tearDown(self):
        self.cleanup()

    def _write_plan(self, approved_files):
        """Write approved-plan.txt with the given list of paths."""
        content = "\n".join(approved_files) + "\n"
        _stage_file(self.repo, PLAN_FILE, content)
        # Commit the plan so it exists in the repo (not just the index).
        _git(["commit", "-m", "init plan", "--allow-empty"], cwd=self.repo, check=False, capture=True)

    def test_blocks_unplanned_file(self):
        """A staged file not in the approved plan is blocked.

        The block message names the unplanned file.
        """
        self._write_plan(["iris/proxy.py", "README.md"])
        _stage_file(self.repo, "iris/surprise.py", "# not planned\n")
        rc, _out, err = _run_hook(HOOK_PLAN, self.repo)
        self.assertNotEqual(rc, 0, "Hook should block an unplanned staged file")
        self.assertIn("iris/surprise.py", err, "Block message must name the unplanned file. stderr: " + err)

    def test_passes_planned_file(self):
        """A staged file that is in the approved plan passes."""
        self._write_plan(["iris/proxy.py", "README.md"])
        _stage_file(self.repo, "iris/proxy.py", "# planned\n")
        rc, _out, err = _run_hook(HOOK_PLAN, self.repo)
        self.assertEqual(rc, 0, f"Hook should pass a planned file. stderr: {err!r}")

    def test_passes_when_plan_file_itself_is_staged(self):
        """Staging the plan file covers any new unplanned files.

        When approved-plan.txt is itself in the staged files, the hook
        treats the new plan as the authority and allows the commit.
        This is the escape hatch for adding new files and updating the plan
        in one commit.
        """
        self._write_plan(["iris/proxy.py"])
        # Stage a new file not yet in the plan.
        _stage_file(self.repo, "iris/new_feature.py", "# new\n")
        # Also update and stage the plan file to include the new file.
        updated_plan = "iris/proxy.py\niris/new_feature.py\n"
        _stage_file(self.repo, PLAN_FILE, updated_plan)
        rc, _out, err = _run_hook(HOOK_PLAN, self.repo)
        self.assertEqual(rc, 0, f"Hook should pass when the plan file is staged. stderr: {err!r}")

    def test_checks_deleted_files_against_plan(self):
        _stage_file(self.repo, "src/unplanned.py", "# tracked\n")
        self._write_plan(["README.md"])
        _git(["rm", "src/unplanned.py"], self.repo)
        rc, _, err = _run_hook(HOOK_PLAN, self.repo)
        self.assertEqual(rc, 1)
        self.assertIn("src/unplanned.py", err)

    def test_blocks_staged_plan_deletion(self):
        self._write_plan(["README.md"])
        _git(["rm", PLAN_FILE], self.repo)
        rc, _, err = _run_hook(HOOK_PLAN, self.repo)
        self.assertEqual(rc, 1)
        self.assertIn(PLAN_FILE, err)


class TestHookInstaller(unittest.TestCase):
    def setUp(self):
        self.repo, cleanup = _make_temp_repo()
        self.addCleanup(cleanup)
        self.installer = runpy.run_path(INSTALLER)
        self.install = self.installer["install"]

    def test_backs_up_existing_hook_once(self):
        original = Path(self.repo, ".git", "hooks", "pre-commit")
        original.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.install(self.repo)
        self.install(self.repo)
        self.assertEqual(original.with_name("pre-commit.iris-backup").read_bytes(), b"#!/bin/sh\nexit 0\n")

    def test_installs_in_configured_hooks_path(self):
        _git(["config", "core.hooksPath", "local hooks"], self.repo)
        self.assertEqual(Path(self.install(self.repo)), Path(self.repo, "local hooks"))
        _stage_file(self.repo, "attempts.db")
        commit = _git(["commit", "-m", "check hook"], self.repo, check=False)
        self.assertNotEqual(commit.returncode, 0)
        self.assertIn("iris-check-banned-files", commit.stderr)

    def test_installs_in_common_hooks_path_from_linked_worktree(self):
        _git(["commit", "--allow-empty", "-m", "initial"], self.repo)
        linked_tree = Path(self.repo, "linked")
        _git(["worktree", "add", "-b", "linked", str(linked_tree)], self.repo)
        self.assertEqual(Path(self.install(str(linked_tree))), Path(self.repo, ".git", "hooks"))

    def test_installer_reports_write_errors(self):
        main = self.installer["main"]
        with patch.dict(main.__globals__, {"install": lambda: self._raise_permission_error()}):
            with patch("sys.stderr") as stderr:
                self.assertEqual(main(), 1)
                self.assertIn("locked test hook", stderr.write.call_args.args[0])

    @staticmethod
    def _raise_permission_error():
        raise PermissionError("locked test hook")

    def test_wrapper_preserves_literal_interpreter_path(self):
        python = "C:/Test Area/Codex's Interpreter/python.exe"
        hooks_dir = Path(self.install(self.repo, python))
        wrapper = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
        assignment = next(line for line in wrapper.splitlines() if line.startswith("PYTHON_EXE="))
        self.assertEqual(shlex.split(assignment), [f"PYTHON_EXE={python}"])
        self.assertTrue(assignment.startswith("PYTHON_EXE='"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
