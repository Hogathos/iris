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

import codecs
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

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

# Quoted strings stay together when checking shell command arguments.
SHELL_TOKENS = re.compile(r"""[;&|()\r\n]+|(?:'[^']*'|"(?:\\.|[^"\\])*"|[^ \t\r\n;&|()'"])+""")
GIT_GLOBAL_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
GIT_BANNED_OPTIONS = {
    "add": ("git add -A", {"-A", "--all"}),
    "commit": ("git commit -a", {"-a", "-am", "--all"}),
    "push": ("git push --force", {"-f", "--force"}),
    "reset": ("git reset --hard", {"--hard"}),
}

# File ban rules matching hooks/iris-check-banned-files.py and AGENTS.md.
BANNED_DIRS = {"dist", "build", ".pytest_cache"}
BANNED_EXTENSIONS = {".exe", ".db", ".spec", ".pem", ".p12", ".pfx", ".key"}
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


def _resolve_repo_root(cwd: str) -> str:
    """Find the repository root, or use cwd for a standalone directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        check=False,
        encoding="utf-8",
    )
    return result.stdout.rstrip("\r\n") if result.returncode == 0 else cwd


def _normalize_target_path(raw_path: str, cwd: str, repo_root: str) -> str:
    """Convert raw_path into a clean POSIX-style path relative to repo_root when inside it."""
    cleaned = _strip_win_unc_prefix(raw_path).replace("\\", "/")
    target = os.path.abspath(os.path.join(cwd, cleaned))
    try:
        relative = os.path.relpath(target, repo_root)
    except ValueError:
        relative = target
    return os.path.normcase(relative).replace("\\", "/")


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


def _load_plan(plan_path: str) -> set[str]:
    """Load approved repo-relative paths from approved-plan.txt."""
    raw = Path(plan_path).read_bytes()
    encoding = "utf-16" if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
    paths = (line.strip() for line in raw.decode(encoding).splitlines())
    return {
        posixpath.normpath(os.path.normcase(path).replace("\\", "/")) for path in paths if path and not path.startswith("#")
    }


def _check_git_arguments(arguments: list[str]) -> str | None:
    """Check options on the Git subcommand, after global options."""
    words = iter(arguments)
    for word in words:
        if word in GIT_GLOBAL_OPTIONS_WITH_VALUE:
            next(words, None)
        elif not word.startswith("-"):
            break
    else:
        return None

    rule = GIT_BANNED_OPTIONS.get(word)
    if rule is None:
        return None
    label, banned_options = rule
    for arg in words:
        if arg == "--":
            break
        if word == "commit" and arg in {"-m", "--message", "-F", "--file"}:
            next(words, None)
        elif arg in banned_options:
            return f"Denied banned shell pattern '{label}'."
    return None


def _check_remove_arguments(command: str, arguments: list[str], cwd: str) -> str | None:
    """Check direct removal of the plan and recursive forced removal."""
    options = set()
    for arg in arguments:
        if arg == "--":
            break
        if arg.startswith("-"):
            options.add(arg.lower())
    short_flags = {flag for arg in options - {"-recurse", "-force"} if re.fullmatch(r"-[a-z]+", arg) for flag in arg[1:]}
    if command == "rm" and (("r" in short_flags or "--recursive" in options) and ("f" in short_flags or "--force" in options)):
        return "Denied banned shell pattern 'rm -rf'."
    if command in {"rm", "remove-item"} and {"-recurse", "-force"} <= options:
        return "Denied recursive forced removal."

    root = _resolve_repo_root(cwd)
    for arg in arguments:
        if _normalize_target_path(arg, cwd, root) == PLAN_FILENAME:
            return f"Denied removal of {PLAN_FILENAME}. A human must update the plan."
    return None


def _check_shell_words(words: list[str], cwd: str) -> str | None:
    """Check rules for one shell command without logging its arguments."""
    if not words:
        return None
    executable = posixpath.basename(words[0].replace("\\", "/")).lower().removesuffix(".exe")
    if executable == "git":
        return _check_git_arguments(words[1:])
    if executable in {"rm", "remove-item"}:
        return _check_remove_arguments(executable, words[1:], cwd)
    return None


def _check_shell_command(command: str, cwd: str) -> str | None:
    """Check each command with quoted arguments kept as single tokens."""
    words: list[str] = []
    for token in SHELL_TOKENS.findall(command):
        if token[0] in ";&|()\r\n":
            deny_reason = _check_shell_words(words, cwd)
            if deny_reason is not None:
                return deny_reason
            words.clear()
        else:
            words.append(token.strip("'\""))
    return _check_shell_words(words, cwd)


def _write_targets(tool_name: str, tool_input: dict) -> list[str]:
    """Extract every target from the supported file mutation payloads."""
    if tool_name == "apply_patch":
        patch = tool_input.get("input")
        if not isinstance(patch, str):
            return []
        return re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): ([^\r\n]+)", patch, re.MULTILINE)

    targets: list[str] = []
    for key in ("path", "file_path", "filename", "notebook_path", "notebookPath"):
        if key in tool_input:
            value = tool_input[key]
            if not isinstance(value, str) or not value.strip():
                return []
            targets.append(value)
    return targets


def _check_file_mutation(raw_path: str, cwd: str, repo_root: str) -> str | None:
    """Return a deny reason if writing/editing raw_path violates ban or plan rules."""
    norm_path = _normalize_target_path(raw_path, cwd, repo_root)
    resolved_path = _normalize_target_path(
        os.path.realpath(os.path.join(cwd, _strip_win_unc_prefix(raw_path))), cwd, os.path.realpath(repo_root)
    )
    if PLAN_FILENAME in {norm_path, resolved_path}:
        return f"Denied change to {PLAN_FILENAME}. A human must update the plan."

    ban_reason = _check_banned_path(norm_path)
    if ban_reason is not None:
        return f"Denied write to banned file '{norm_path}' ({ban_reason})."

    plan_path = os.path.join(repo_root, PLAN_FILENAME)
    if not os.path.isfile(plan_path):
        return f"Denied write because {PLAN_FILENAME} is missing."
    approved_files = _load_plan(plan_path)
    if norm_path not in approved_files:
        return f"Denied write to unplanned file '{norm_path}' (not listed in {PLAN_FILENAME})."

    return None


def evaluate_payload(payload: dict, default_cwd: str) -> str | None:
    """Evaluate a parsed PreToolUse payload and return a deny reason or None."""
    raw_tool_name = str(payload.get("tool_name") or "")
    tool_name = raw_tool_name.rsplit(".", 1)[-1].rsplit("__", 1)[-1].strip().lower()
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    cwd = _strip_win_unc_prefix(str(payload.get("cwd") or default_cwd or os.getcwd()))

    if tool_name in SHELL_TOOL_NAMES:
        command = str(tool_input.get("command") or "")
        return _check_shell_command(command, cwd)

    if tool_name in WRITE_TOOL_NAMES:
        targets = _write_targets(tool_name, tool_input)
        if not targets:
            return "Denied write because the target path could not be determined."
        repo_root = _resolve_repo_root(cwd)
        for target in targets:
            deny_reason = _check_file_mutation(target, cwd, repo_root)
            if deny_reason is not None:
                return deny_reason

    return None


def _run() -> int:
    """Read and evaluate one PreToolUse payload."""
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


def main() -> int:
    """Deny when any part of the hook cannot complete."""
    try:
        return _run()
    except Exception:
        return _deny("Denied tool call because the hook could not complete its checks.")


if __name__ == "__main__":
    sys.exit(main())
