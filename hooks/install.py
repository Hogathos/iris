#!/usr/bin/env python3
"""Install Iris pre-commit hooks into the active git working tree's .git/hooks/.

Copies the three gate scripts:
  - iris-check-banned-files.py
  - iris-check-lint.py
  - iris-check-plan.py
into ``<git-dir>/hooks/`` and writes a ``pre-commit`` entrypoint that invokes
all three in sequence using the installing Python interpreter.

Works on Windows (Git Bash) and POSIX platforms. Re-running updates the
installed hooks in place.
"""

import os
import shutil
import stat
import subprocess
import sys

HOOK_SCRIPTS = (
    "iris-check-banned-files.py",
    "iris-check-lint.py",
    "iris-check-plan.py",
)


def _resolve_git_hooks_dir(repo_root: str) -> str:
    """Return the absolute path to .git/hooks for repo_root (worktree-safe)."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    git_dir = result.stdout.strip()
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(repo_root, git_dir)
    return os.path.join(os.path.abspath(git_dir), "hooks")


def install(repo_root: str | None = None, python_executable: str | None = None) -> str:
    """Copy hook scripts and write the pre-commit wrapper into .git/hooks/."""
    source_dir = os.path.dirname(os.path.abspath(__file__))
    if repo_root is None:
        repo_root = os.path.dirname(source_dir)
    python_exe = (python_executable or sys.executable).replace("\\", "/")

    hooks_dir = _resolve_git_hooks_dir(repo_root)
    os.makedirs(hooks_dir, exist_ok=True)

    for script_name in HOOK_SCRIPTS:
        src = os.path.join(source_dir, script_name)
        dst = os.path.join(hooks_dir, script_name)
        shutil.copy2(src, dst)
        # Ensure executable bit on POSIX.
        current_mode = os.stat(dst).st_mode
        os.chmod(dst, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # POSIX shell script executed by git on both Windows (Git Bash) and Unix.
    pre_commit_path = os.path.join(hooks_dir, "pre-commit")
    hook_dir_posix = hooks_dir.replace("\\", "/")
    lines = [
        "#!/bin/sh",
        "# Installed by hooks/install.py — deterministic pre-commit gates for Iris.",
        "set -e",
        f'PYTHON_EXE="{python_exe}"',
        'if [ ! -x "$PYTHON_EXE" ]; then',
        "  if command -v python3 >/dev/null 2>&1; then",
        '    PYTHON_EXE="python3"',
        "  else",
        '    PYTHON_EXE="python"',
        "  fi",
        "fi",
    ]
    for script_name in HOOK_SCRIPTS:
        lines.append(f'"$PYTHON_EXE" "{hook_dir_posix}/{script_name}"')

    with open(pre_commit_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")

    current_mode = os.stat(pre_commit_path).st_mode
    os.chmod(pre_commit_path, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hooks_dir


def main() -> int:
    """CLI entrypoint for installing Iris git pre-commit hooks."""
    try:
        hooks_dir = install()
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"Failed to locate git hooks directory: {exc}\n")
        return 1
    sys.stdout.write(f"Installed Iris pre-commit hooks into {hooks_dir}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
