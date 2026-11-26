#!/usr/bin/env python3
"""Pre-commit hook 2: run fast lint and type checks before committing.

Runs three checks at the repository root:
  1. ``ruff check .``
  2. ``ruff format --check .``
  3. ``pyright``

Blocks the commit (non-zero exit) if any tool exits non-zero, naming the
failing tool on stderr and forwarding its output. The full pytest suite
remains in CI, not in this hook.
"""

import importlib.util
import os
import shutil
import subprocess
import sys


def _resolve_repo_root() -> str:
    """Return the git working-tree root, falling back to current directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return os.getcwd()


def _resolve_tool_cmd(module_name: str, exe_name: str) -> list[str] | None:
    """Find how to invoke ``module_name`` / ``exe_name``.

    Prefers ``sys.executable -m <module>`` when installed in the active Python
    environment, then a sibling executable next to ``sys.executable``, then
    ``shutil.which``. Returns None if the tool is not installed.
    """
    if importlib.util.find_spec(module_name) is not None:
        return [sys.executable, "-m", module_name]

    scripts_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates = [f"{exe_name}.exe", exe_name] if os.name == "nt" else [exe_name]
    for candidate in candidates:
        sibling = os.path.join(scripts_dir, candidate)
        if os.path.isfile(sibling):
            return [sibling]

    on_path = shutil.which(exe_name)
    if on_path:
        return [on_path]
    return None


def _run_gate(label: str, cmd: list[str], cwd: str) -> tuple[bool, str]:
    """Run one lint/type command and return (passed, combined_output)."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return result.returncode == 0, combined


def _get_staged_python_files(repo_root: str) -> list[str]:
    """Return staged non-deleted .py file paths relative to repo_root."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=d", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        err_text = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git diff --cached failed: {err_text}")
    raw = result.stdout.decode("utf-8", errors="replace")
    return [entry for entry in raw.split("\0") if entry.endswith(".py")]


def main() -> int:
    """Run ruff check, ruff format --check, and pyright on staged .py files."""
    repo_root = _resolve_repo_root()
    try:
        staged_py = _get_staged_python_files(repo_root)
    except RuntimeError as exc:
        sys.stderr.write(f"iris-check-lint: {exc}\n")
        return 1

    # No staged Python files: nothing to lint.
    if not staged_py:
        return 0

    ruff_cmd = _resolve_tool_cmd("ruff", "ruff")
    if ruff_cmd is None:
        sys.stderr.write("Commit blocked by iris-check-lint: ruff is not installed.\n")
        return 1

    pyright_cmd = _resolve_tool_cmd("pyright", "pyright")
    if pyright_cmd is None:
        sys.stderr.write("Commit blocked by iris-check-lint: pyright is not installed.\n")
        return 1

    # Pass --pythonpath so pyright resolves packages from the active venv even
    # when VIRTUAL_ENV is not exported in the hook's shell environment.
    checks: list[tuple[str, list[str]]] = [
        ("ruff check", ruff_cmd + ["check"] + staged_py),
        ("ruff format --check", ruff_cmd + ["format", "--check"] + staged_py),
        ("pyright", pyright_cmd + ["--pythonpath", sys.executable] + staged_py),
    ]

    for label, cmd in checks:
        ok, output = _run_gate(label, cmd, repo_root)
        if not ok:
            sys.stderr.write(f"Commit blocked by iris-check-lint ({label} failed):\n")
            if output:
                sys.stderr.write(output + "\n")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
