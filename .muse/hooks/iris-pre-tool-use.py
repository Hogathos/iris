#!/usr/bin/env python3
"""Muse Code PreToolUse hook: deterministic gate before any tool call runs.

Reads the PreToolUse JSON payload on stdin, enforces the repository rules,
and denies violating calls with exit code 2 plus ``hookSpecificOutput`` JSON on
stdout and the reason on stderr. Clean tool calls exit 0 with empty stdout.

Verified against Muse Code 1.4.25 (``muse exec`` non-interactive sessions):
  - Shell tools arrive as ``powershell`` (or ``shell`` / ``bash``) with
    ``tool_input.command``.
  - File write/edit tools arrive as ``write_file`` / ``edit_file`` with
    ``tool_input.path``.
  - Read tools arrive as ``read_file`` with ``tool_input.path``.
  - Claude Code names (``Bash``, ``Write``, ``Edit``, ``Read``) and
    ``tool_input.file_path`` are also accepted as compatibility aliases.
"""

import fnmatch
import json
import os
import posixpath
import re
import sys

PLAN_FILENAME = "approved-plan.txt"

# Shell tool names (after stripping any optional "muse." namespace prefix).
SHELL_TOOL_NAMES = {"powershell", "shell", "bash", "exec_command"}

# File mutation tool names (after stripping any optional "muse." prefix).
WRITE_TOOL_NAMES = {
    "write_file",
    "edit_file",
    "apply_patch",
    "delete_file",
    "write",
    "edit",
    "multiedit",
    "notebookedit",
}

# Shell command ban patterns from specs/hooks/requirements.md.
SHELL_BAN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "rm -rf",
        re.compile(r"(?:^|[\s;&|()])rm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)(?:\s|$)"),
    ),
    (
        "git add -A",
        re.compile(r"\bgit\s+add\b[^\n;&|]*\s(?:-A|--all)(?:\s|$)"),
    ),
    (
        "git commit -a",
        re.compile(r"\bgit\s+commit\b[^\n;&|]*\s-a(?:m)?(?:\s|$)"),
    ),
    (
        "git push --force",
        re.compile(r"\bgit\s+push\b[^\n;&|]*\s(?:--force|-f)(?:\s|$)"),
    ),
    (
        "git reset --hard",
        re.compile(r"\bgit\s+reset\b[^\n;&|]*\s--hard(?:\s|$)"),
    ),
)

# File ban rules matching hooks/iris-check-banned-files.py and AGENTS.md.
BANNED_DIRS = {"dist", "build", ".pytest_cache"}
BANNED_EXTENSIONS = {".exe", ".db", ".spec", ".pem", ".p12", ".pfx", ".key"}
SECRET_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "*_secret*",
    "*_key*",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
)
ALLOWED_ROOT_FILES = {"iris.spec"}


def _deny(reason: str) -> int:
    """Emit Muse/Claude Code PreToolUse deny output and return exit code 2."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    sys.stderr.write(reason + "\n")
    return 2


def _strip_win_unc_prefix(path: str) -> str:
    """Strip Windows extended-length UNC prefixes such as ``\\\\?\\``."""
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _normalize_target_path(raw_path: str, repo_root: str) -> str:
    """Convert raw_path into a clean POSIX-style path relative to repo_root when inside it."""
    cleaned = _strip_win_unc_prefix(raw_path.strip())
    clean_root = _strip_win_unc_prefix(os.path.abspath(repo_root))

    if os.path.isabs(cleaned):
        abs_target = os.path.normcase(os.path.abspath(cleaned))
        abs_root = os.path.normcase(clean_root)
        try:
            rel = os.path.relpath(abs_target, abs_root)
        except ValueError:
            rel = cleaned
        # Keep paths outside repo_root distinguishable rather than resolving inside.
        if not rel.startswith(".."):
            cleaned = rel

    posix_path = cleaned.replace("\\", "/")
    while posix_path.startswith("./"):
        posix_path = posix_path[2:]
    return posixpath.normpath(posix_path) if posix_path else ""


def _check_banned_path(norm_path: str) -> str | None:
    """Return a ban reason if norm_path matches the repo ban list, else None."""
    if not norm_path or norm_path == ".":
        return None
    if norm_path in ALLOWED_ROOT_FILES:
        return None

    parts = norm_path.split("/")
    dir_parts = parts[:-1]
    basename = parts[-1]
    lower_name = basename.lower()

    for segment in dir_parts:
        if segment in BANNED_DIRS:
            return f"banned directory '{segment}/'"

    _, ext = posixpath.splitext(lower_name)
    if ext in BANNED_EXTENSIONS:
        return f"banned extension '{ext}'"

    for pattern in SECRET_NAME_PATTERNS:
        if fnmatch.fnmatch(lower_name, pattern):
            return f"secret filename pattern '{pattern}'"

    return None


def _load_plan(plan_path: str) -> set[str]:
    """Load approved repo-relative paths from approved-plan.txt."""
    approved: set[str] = set()
    with open(plan_path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            posix_line = line.replace("\\", "/")
            while posix_line.startswith("./"):
                posix_line = posix_line[2:]
            approved.add(posixpath.normpath(posix_line))
    return approved


def _check_shell_command(command: str) -> str | None:
    """Return a deny reason if command matches any shell ban pattern."""
    for label, pattern in SHELL_BAN_PATTERNS:
        if pattern.search(command):
            return f"Denied banned shell pattern '{label}' in command: {command}"
    return None


def _check_file_mutation(raw_path: str, repo_root: str) -> str | None:
    """Return a deny reason if writing/editing raw_path violates ban or plan rules."""
    if not raw_path:
        return None

    norm_path = _normalize_target_path(raw_path, repo_root)
    ban_reason = _check_banned_path(norm_path)
    if ban_reason is not None:
        return f"Denied write to banned file '{norm_path}' ({ban_reason})."

    # Writing approved-plan.txt itself updates the plan and is always allowed.
    if norm_path == PLAN_FILENAME:
        return None

    plan_path = os.path.join(repo_root, PLAN_FILENAME)
    if os.path.isfile(plan_path):
        approved_files = _load_plan(plan_path)
        if norm_path not in approved_files:
            return (
                f"Denied write to unplanned file '{norm_path}' "
                f"(not listed in {PLAN_FILENAME})."
            )

    return None


def evaluate_payload(payload: dict, default_cwd: str) -> str | None:
    """Evaluate a parsed PreToolUse payload and return a deny reason or None."""
    raw_tool_name = str(payload.get("tool_name") or "")
    tool_name = raw_tool_name.split(".")[-1].strip().lower()
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    repo_root = str(payload.get("cwd") or default_cwd or os.getcwd())
    repo_root = _strip_win_unc_prefix(repo_root)

    if tool_name in SHELL_TOOL_NAMES:
        command = str(tool_input.get("command") or "")
        return _check_shell_command(command)

    if tool_name in WRITE_TOOL_NAMES:
        raw_path = str(tool_input.get("path") or tool_input.get("file_path") or "")
        return _check_file_mutation(raw_path, repo_root)

    return None


def main() -> int:
    """Read PreToolUse payload on stdin and allow (0) or deny (2)."""
    raw_stdin = sys.stdin.read()
    if not raw_stdin.strip():
        return _deny("Denied tool call: empty PreToolUse payload on stdin.")

    try:
        payload = json.loads(raw_stdin)
    except json.JSONDecodeError as exc:
        return _deny(f"Denied tool call: invalid PreToolUse JSON payload ({exc}).")

    if not isinstance(payload, dict):
        return _deny("Denied tool call: PreToolUse payload must be a JSON object.")

    deny_reason = evaluate_payload(payload, os.getcwd())
    if deny_reason is not None:
        return _deny(deny_reason)

    # Clean allow: exit 0 with empty stdout (Muse rejects bare allow without updatedInput).
    return 0


if __name__ == "__main__":
    sys.exit(main())
