#!/usr/bin/env python3
"""Pre-commit hook 3: block staged files missing from approved-plan.txt.

Reads ``approved-plan.txt`` at the repository root (one relative file path per
line). If any staged non-deleted file is not listed in the plan, the commit is
blocked and the unplanned files are printed to stderr.

Escape hatch: if ``approved-plan.txt`` itself is staged in the same commit, the
updated plan covers the newly staged files and the check passes. If no
``approved-plan.txt`` exists in the repository, no active plan gate applies.
"""

import os
import posixpath
import subprocess
import sys

PLAN_FILENAME = "approved-plan.txt"


def _normalize_rel_path(path: str) -> str:
    """Normalize path separators to POSIX form without leading ./."""
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return posixpath.normpath(normalized) if normalized else ""


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


def _get_staged_files(repo_root: str) -> list[str]:
    """Query git for staged non-deleted file paths relative to repo_root."""
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
    return [_normalize_rel_path(entry) for entry in raw.split("\0") if entry]


def _load_plan(plan_path: str) -> set[str]:
    """Read approved relative file paths from approved-plan.txt."""
    approved: set[str] = set()
    with open(plan_path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            approved.add(_normalize_rel_path(line))
    return approved


def main() -> int:
    """Verify every staged file belongs to approved-plan.txt unless plan is staged."""
    repo_root = _resolve_repo_root()
    try:
        staged_files = _get_staged_files(repo_root)
    except RuntimeError as exc:
        sys.stderr.write(f"iris-check-plan: {exc}\n")
        return 1

    if not staged_files:
        return 0

    # Escape hatch: staging approved-plan.txt updates the plan alongside new files.
    if PLAN_FILENAME in staged_files:
        return 0

    plan_path = os.path.join(repo_root, PLAN_FILENAME)
    if not os.path.isfile(plan_path):
        # No active approved-plan.txt in the repo means no plan list to enforce yet.
        return 0

    approved_files = _load_plan(plan_path)
    unplanned = [path for path in staged_files if path not in approved_files]

    if unplanned:
        sys.stderr.write(f"Commit blocked by iris-check-plan (staged files missing from {PLAN_FILENAME}):\n")
        for path in unplanned:
            sys.stderr.write(f"  - {path}\n")
        sys.stderr.write(f"Update and stage {PLAN_FILENAME} alongside the commit if these files are approved.\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
