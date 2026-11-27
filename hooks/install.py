#!/usr/bin/env python3
"""Install Iris pre-commit hooks into the active Git hooks directory.

Copies the three gate scripts:
  - iris-check-banned-files.py
  - iris-check-lint.py
  - iris-check-plan.py
into the Git hooks directory and writes a pre-commit entrypoint that invokes
all three in sequence using the installing Python interpreter.

Works on Windows (Git Bash) and POSIX platforms. Re-running updates the
installed Iris hooks in place. The installer backs up an existing custom hook.
"""

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

HOOK_SCRIPTS = (
    "iris-check-banned-files.py",
    "iris-check-lint.py",
    "iris-check-plan.py",
)
INSTALL_MARKER = "# Installed by hooks/install.py"


def _resolve_git_hooks_dir(repo_root: str) -> str:
    """Return the active hooks path, including Git configuration and worktrees."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=repo_root,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return os.path.realpath(os.path.join(repo_root, result.stdout.rstrip("\r\n")))


def _back_up_hook(path: str, replacement: bytes) -> None:
    """Preserve the current hook if the installer would change it."""
    try:
        with open(path, "rb") as existing:
            original = existing.read()
    except FileNotFoundError:
        return
    if original == replacement:
        return

    backup = path + ".iris-backup"
    suffix = 0
    while True:
        candidate = f"{backup}.{suffix}" if suffix else backup
        try:
            with open(candidate, "xb") as target:
                target.write(original)
        except FileExistsError:
            suffix += 1
        else:
            shutil.copystat(path, candidate)
            return


def install(repo_root: str | None = None, python_executable: str | None = None) -> str:
    """Copy hook scripts and write the wrapper into the active hooks directory."""
    source_dir = os.path.dirname(os.path.abspath(__file__))
    if repo_root is None:
        repo_root = os.path.dirname(source_dir)
    python_exe = python_executable or sys.executable
    if os.name == "nt":
        python_exe = python_exe.replace("\\", "/")

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
    lines = [
        "#!/bin/sh",
        INSTALL_MARKER,
        "set -e",
        f"PYTHON_EXE={shlex.quote(python_exe)}",
        'if [ ! -x "$PYTHON_EXE" ]; then',
        "  if command -v python3 >/dev/null 2>&1; then",
        '    PYTHON_EXE="python3"',
        "  else",
        '    PYTHON_EXE="python"',
        "  fi",
        "fi",
    ]
    for script_name in HOOK_SCRIPTS:
        script_path = os.path.join(hooks_dir, script_name)
        if os.name == "nt":
            script_path = script_path.replace("\\", "/")
        lines.append(f'"$PYTHON_EXE" {shlex.quote(script_path)}')

    wrapper = ("\n".join(lines) + "\n").encode("utf-8")
    _back_up_hook(pre_commit_path, wrapper)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=hooks_dir, delete=False) as fh:
            temp_path = fh.name
            fh.write(wrapper)
        current_mode = os.stat(temp_path).st_mode
        os.chmod(temp_path, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(temp_path, pre_commit_path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)
    return hooks_dir


def main() -> int:
    """CLI entrypoint for installing Iris git pre-commit hooks."""
    try:
        hooks_dir = install()
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"Failed to locate git hooks directory: {exc}\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"Failed to install Iris pre-commit hooks: {exc}\n")
        return 1
    sys.stdout.write(f"Installed Iris pre-commit hooks into {hooks_dir}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
