# Hooks as deterministic gates — test-writing prompt (Step 1 only)

Branch: `tooling/hooks-deterministic-gates`. Base: main `37b4952`.

## Setup (do this first)
Fetch origin FIRST, rebase onto origin/main, verify and report the base
commit. The spec is `specs/hooks/requirements.md` on this branch — read it
before writing any test. Do not implement anything yet.

## Challenge the frame
Before writing tests, if a simpler or sturdier way to test these hooks
exists, say so once, up front, in the report. Then do what the spec says.

## Step 1 only: write the failing tests, then STOP
Write failing tests for all four hooks. Commit them locally. STOP for test
review. Do not implement the hooks. Do not push.

Test files:
- `tests/test_hooks.py` — the three pre-commit hooks.
- `tests/test_muse_hooks.py` — the Muse Code PreToolUse hook.

Tests to write (each must fail before the hooks exist and pass after):
1. A commit staging a banned file (`.exe`, a `*.db`, a secret by filename)
   is blocked, and the block names the file.
2. A commit staging only clean files passes.
3. A commit with a lint failure (`ruff check`), a format failure
   (`ruff format --check`), or a type failure (`pyright`) is blocked, and
   the block names the failure.
4. A commit staging a file not in the approved plan is blocked.
5. A commit that also stages the plan file passes (updating the plan covers
   the new files).
6. The PreToolUse hook denies a shell command matching a ban pattern
   (`rm -rf`, `git add -A`, `git commit -a`, `git push --force`,
   `git reset --hard`).
7. The PreToolUse hook denies a write to a banned file.
8. The PreToolUse hook denies a write to a file not in the approved plan.
9. The PreToolUse hook allows a clean tool call.

Verification points to pin down in the tests (do not guess):
- Whether the PreToolUse hook fires in `muse exec` non-interactive mode
  (the watcher's invocation). Test with the watcher's exact command.
- The exact PreToolUse payload shape (tool name and parameters on stdin)
  and the deny output shape. Verify against the official Muse Code docs
  ("Extending and automating", dev.meta.ai).

## Rules (do not break these)
- Stage named files only. Never `git add -A`. Never `git commit -a`.
- Never commit `.exe`, `dist/`, `build/`, `*.db`, `.pytest_cache/`,
  `*.spec`, or secrets. (The already-tracked `iris.spec` is the one
  exception, and it is already in the repo.)
- Keep verbose comments and docstrings. Do not strip them.
- Short sentences, one idea per sentence, active voice, no em dash.

## Report
Write your report to `prompts/tooling-hooks-test-report.md` on this branch
(own commit). The report names: the base commit, the test files, each test
and what it checks, the verification points and what you found, and what is
still open. Then stop. Do not implement. Do not push.
