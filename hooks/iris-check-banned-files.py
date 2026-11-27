#!/usr/bin/env python3
"""Pre-commit hook 1: block staged files matching the Iris ban list.

Reads the staged file list via ``git diff --cached --name-only --diff-filter=d -z``
and rejects any path matching the repository ban rules from AGENTS.md:
  - .exe executables
  - dist/, build/, .pytest_cache/ directories
  - *.db and demo_*.db SQLite databases
  - *.spec PyInstaller specs (except the tracked ``iris.spec`` at repo root)
  - common secret filenames (.env, .env.*, secret.txt, secrets.json,
    credentials.json, token.txt, *.pem, *.p12, *.pfx, *.key, id_rsa, id_ed25519)
  - .env.example is an allowed example filename outside banned directories

Exits 0 if all staged files are clean, or 1 with offending paths on stderr.
"""

import fnmatch
import os
import posixpath
import subprocess
import sys

# Directory names whose contents must never be committed.
BANNED_DIRS = {"dist", "build", ".pytest_cache"}

# File extensions that must never be committed (lowercase).
BANNED_EXTENSIONS = {".exe", ".db", ".spec", ".pem", ".p12", ".pfx", ".key"}

# Filename glob patterns for secrets and credentials (matched lowercase on basename).
SECRET_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "secret.txt",
    "secrets.json",
    "credentials.json",
    "token.txt",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
)

# Single tracked PyInstaller spec explicitly exempted by AGENTS.md.
ALLOWED_ROOT_FILES = {"iris.spec"}


def _normalize_rel_path(path: str) -> str:
    """Return a clean POSIX-style repo-relative path without leading ./."""
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return posixpath.normpath(normalized) if normalized else ""


def check_banned_path(rel_path: str) -> str | None:
    """Return a short reason if ``rel_path`` is banned, or None if allowed."""
    norm = _normalize_rel_path(rel_path)
    if not norm or norm == ".":
        return None

    # Exact repo-root iris.spec is the single allowed .spec exception.
    if norm in ALLOWED_ROOT_FILES:
        return None

    parts = norm.split("/")
    dir_parts = parts[:-1]
    basename = parts[-1]
    lower_name = basename.lower()

    for segment in dir_parts:
        if segment.lower() in BANNED_DIRS:
            return f"banned directory '{segment}/'"

    _, ext = posixpath.splitext(lower_name)
    if ext in BANNED_EXTENSIONS:
        return f"banned extension '{ext}'"

    if lower_name == ".env.example":
        return None
    for pattern in SECRET_NAME_PATTERNS:
        if fnmatch.fnmatch(lower_name, pattern):
            return "banned secret-file pattern"

    return None


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
    return [entry for entry in raw.split("\0") if entry]


def main() -> int:
    """Check staged files and block the commit if any file is banned."""
    repo_root = os.getcwd()
    try:
        staged_files = _get_staged_files(repo_root)
    except RuntimeError as exc:
        sys.stderr.write(f"iris-check-banned-files: {exc}\n")
        return 1

    violations: list[tuple[str, str]] = []
    for path in staged_files:
        reason = check_banned_path(path)
        if reason is not None:
            violations.append((path, reason))

    if violations:
        sys.stderr.write("Commit blocked by iris-check-banned-files (banned files staged):\n")
        for path, reason in violations:
            sys.stderr.write(f"  - {path} ({reason})\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
