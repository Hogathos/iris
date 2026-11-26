# Hooks as deterministic gates: test-writing report (Step 1)

**Branch:** `tooling/hooks-deterministic-gates`
**Base commit:** `37b4952` (Merge pull request #62, bridge/watcher-push)
**Branch top before this step:** `5ae302b` (spec + prompt)
**Test commit:** `076f82c`
**Date:** 2026-11-25

---

## Challenge the frame

Before writing, one alternative worth naming:

The three pre-commit hooks could use the `pre-commit` framework instead of
plain git hooks. The framework handles install, skip flags, and CI
integration automatically. The spec says no: plain git hooks and stdlib-only
Python, no new dependency. That is a deliberate decision (Nov 25). Tests
follow the spec.

The alternative for the Muse PreToolUse hook would be to use shell scripts
(bash or PowerShell) instead of Python. The spec says stdlib-only Python to
match the watcher style. Tests follow that decision.

---

## Spec read

Source: `specs/hooks/requirements.md`.

Four hooks:
1. **Banned files** (pre-commit). Blocks `.exe`, `dist/`, `build/`, `*.db`,
   `demo_*.db`, `.pytest_cache/`, `*.spec`, secrets by filename. Exception:
   `iris.spec` at repo root.
2. **Fast lint + types** (pre-commit). Runs `ruff check .`,
   `ruff format --check .`, and `pyright`. Blocks on failure.
3. **Unplanned files** (pre-commit). Blocks staged files not in
   `approved-plan.txt`, unless the plan file itself is staged.
4. **Muse Code PreToolUse hook**. Script at `.muse/hooks/iris-pre-tool-use.py`,
   wired via `.muse/hooks.json`. Reads payload on stdin, exits 2 to deny.

---

## Test files

| File | Hook(s) covered |
|---|---|
| [`tests/test_hooks.py`](file:///C:/Users/Quant/Documents/iris/tests/test_hooks.py) | Hooks 1, 2, 3 (pre-commit) |
| [`tests/test_muse_hooks.py`](file:///C:/Users/Quant/Documents/iris/tests/test_muse_hooks.py) | Hook 4 (Muse PreToolUse) |

---

## Tests written

### test_hooks.py — pre-commit hooks

**Hook 1: TestBannedFilesHook** (8 tests)

| Test | What it checks |
|---|---|
| `test_blocks_exe_file` | Staging `mytool.exe` exits non-zero; stderr names `mytool.exe`. |
| `test_blocks_db_file` | Staging `attempts.db` exits non-zero; stderr names it. |
| `test_blocks_demo_db_file` | Staging `demo_foo.db` exits non-zero; stderr names it. |
| `test_blocks_secret_by_filename_env` | Staging `.env` exits non-zero; stderr names it. |
| `test_blocks_secret_by_filename_pem` | Staging `server.pem` exits non-zero; stderr names it. |
| `test_blocks_spec_file` | Staging `build.spec` exits non-zero; stderr names it. |
| `test_passes_clean_files` | Staging `.py` and `.md` files exits 0. |
| `test_allows_iris_spec_exception` | Staging `iris.spec` at repo root exits 0. |

**Hook 2: TestLintHook** (4 tests)

| Test | What it checks |
|---|---|
| `test_blocks_ruff_check_failure` | File with undefined name fails `ruff check`; hook exits non-zero; stderr mentions ruff. |
| `test_blocks_ruff_format_failure` | File with bad formatting fails `ruff format --check`; hook exits non-zero; stderr mentions ruff. |
| `test_blocks_pyright_failure` | File with a type error fails pyright; hook exits non-zero; stderr mentions pyright. |
| `test_passes_clean_python` | Well-typed, well-formatted `.py` exits 0. |

**Hook 3: TestUnplannedFilesHook** (3 tests)

| Test | What it checks |
|---|---|
| `test_blocks_unplanned_file` | Staging a file absent from `approved-plan.txt` exits non-zero; stderr names the file. |
| `test_passes_planned_file` | Staging a file present in the plan exits 0. |
| `test_passes_when_plan_file_itself_is_staged` | Staging `approved-plan.txt` alongside an unplanned file exits 0. |

---

### test_muse_hooks.py — Muse Code PreToolUse hook

**TestShellCommandBans** (5 tests)

| Test | What it checks |
|---|---|
| `test_denies_rm_rf` | Bash payload with `rm -rf /some/path` is denied (exit 2 or JSON deny). |
| `test_denies_git_add_A` | Bash payload with `git add -A` is denied. |
| `test_denies_git_commit_a` | Bash payload with `git commit -a` is denied. |
| `test_denies_git_push_force` | Bash payload with `git push --force` is denied. |
| `test_denies_git_reset_hard` | Bash payload with `git reset --hard` is denied. |

**TestBannedFileWrites** (6 tests)

| Test | What it checks |
|---|---|
| `test_denies_write_exe` | Write payload to `dist/mytool.exe` is denied. |
| `test_denies_write_db` | Write payload to `attempts.db` is denied. |
| `test_denies_write_secret_env` | Write payload to `.env` is denied. |
| `test_denies_write_secret_pem` | Write payload to `certs/server.pem` is denied. |
| `test_denies_write_spec` | Write payload to `build.spec` is denied. |
| `test_denies_edit_banned_file` | Edit payload targeting `attempts.db` is denied. |

**TestUnplannedFileWrites** (2 tests)

| Test | What it checks |
|---|---|
| `test_denies_write_to_unplanned_file` | Write payload to `iris/surprise.py` is denied when it is absent from `approved-plan.txt`; deny reason names the file. |
| `test_allows_write_to_plan_file_itself` | Write payload to `approved-plan.txt` exits 0 (always allowed). |

**TestCleanToolCalls** (3 tests)

| Test | What it checks |
|---|---|
| `test_allows_clean_bash_command` | Bash payload with `python -m pytest tests/ -q` exits 0. |
| `test_allows_read_of_any_file` | Read payload exits 0 (reads are not writes). |
| `test_allows_write_to_planned_clean_file` | Write payload to `iris/proxy.py` exits 0 when the file is in the plan. |

---

## Verification points

### Point A: Does the PreToolUse hook fire in `muse exec` non-interactive mode?

**What the prompt asked:** Verify whether the hook fires when the watcher
calls `muse exec --non-interactive --prompt "<task>"`.

**What was found:**

The Claude Code hooks reference (docs.anthropic.com, read 2026-11-25) states
that PreToolUse fires for every tool call in auto mode. The exit-2 deny fires
before the tool runs. The reference does not list a flag that disables
PreToolUse hooks in non-interactive mode.

The term "Muse Code" in this repo (from `tests/test_appmode_connect_muse_code.py`)
refers to Meta's `muse` CLI, not the Anthropic product. The
`dev.meta.ai/muse` docs were not reachable at test-writing time. The hooks
spec says `.muse/hooks.json` and `.muse/hooks/iris-pre-tool-use.py`. Those
paths match a Meta Muse layout, not Claude Code's `.claude/settings.json`
layout.

**Open question:** The exact CLI flag spelling (`muse exec` vs `muse run`)
and the exact hook config key (`PreToolUse` vs something Muse-specific) are
unverified. The hook spec names `PreToolUse` as the event and exit 2 as the
deny mechanism. The tests are written to that spec. If Muse's actual hook
protocol differs, the tests will expose the gap.

**What the tests do:** Each test feeds a JSON payload directly to the hook
script as stdin. This does not require a live `muse` session. The test covers
the hook script's logic without depending on the host process. The watcher
invocation path is an open item for integration testing.

### Point B: PreToolUse payload shape

**Source:** Claude Code hooks reference (docs.anthropic.com), confirmed
against the working example `block-rm.sh` in the same doc.

**Stdin fields used by the tests:**

```json
{
  "session_id": "<str>",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash" | "Write" | "Edit" | "Read",
  "tool_input": {
    "command": "<str>",         // Bash only
    "file_path": "<str>",       // Write, Edit, Read
    "content": "<str>",         // Write only
    "old_string": "<str>",      // Edit only
    "new_string": "<str>"       // Edit only
  }
}
```

**Stdout deny shape (hookSpecificOutput):**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "<human-readable reason>"
  }
}
```

**Exit code:** 2 = deny (before tool runs). 0 = no decision or allow.

**Note on Meta Muse vs Claude Code:** The payload shape above is sourced
from Claude Code docs. If Meta Muse uses a different shape, the hook script
and these tests must be updated. That is an open item. The tests pin this
shape explicitly and will fail if the shape changes, which is the right
behaviour.

### Point C: `_is_deny` guard for missing scripts

Python exits with code 2 when it cannot open a script file
(`No such file or directory`). Without a guard, deny-expect tests would
pass for the wrong reason (missing script = exit 2 = treated as deny).
The `_is_deny` helper raises `FileNotFoundError` when it sees that pattern
in stderr, so the test fails with a clear message rather than a false pass.

---

## Hook script paths expected

The tests import or call these paths. None exist yet.

| Path | Hook |
|---|---|
| `hooks/iris-check-banned-files.py` | Hook 1 |
| `hooks/iris-check-lint.py` | Hook 2 |
| `hooks/iris-check-plan.py` | Hook 3 |
| `.muse/hooks/iris-pre-tool-use.py` | Hook 4 |

The plan file convention (hooks 3 and 4): `approved-plan.txt` at repo root.
One file path per line, relative to repo root.

---

## Test run result (before implementation)

```
31 failed in 6.98s
```

All 31 tests fail. Each failure is a `FileNotFoundError` on the missing hook
script or an `AssertionError` on the subprocess exit code. No test passes for
the wrong reason.

---

## What is still open

| Item | Status |
|---|---|
| Meta Muse vs Claude Code hook protocol | Unverified. `dev.meta.ai/muse` docs unreachable. Tests pin the shape from the Claude Code reference as a best guess matching the spec. |
| Exact `muse exec` non-interactive flag | Unverified. Watcher invocation needs an integration test once the hook exists. |
| Secret filename pattern list | The tests cover `.env`, `.pem`. The full list is pinned in the hook script (Step 2). The spec says "secrets by filename pattern" and defers the exact list to the hook. |
| `iris.spec` exception logic | The exception test passes a fresh temp repo (not the real repo). The hook may check tracked status or just the filename. That is an implementation decision for Step 2. |
| `approved-plan.txt` does not exist yet | The plan-related tests create a temporary plan in a temp dir or write it temporarily. Once the plan file is committed to the repo, the clean-call tests in `TestCleanToolCalls` will read the real file. |
| Implementation (Step 2) | Not started. This report covers Step 1 only. No hook scripts exist. |
