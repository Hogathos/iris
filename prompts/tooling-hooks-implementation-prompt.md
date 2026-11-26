# Hooks as deterministic gates — implementation (Step 2)

## Challenge the frame

Before you write code, if a simpler or sturdier way to implement a hook
exists than the one in the spec, say so once, up front, in the report.
Then implement what the spec says. Do not re-decide the spec. The spec is
approved. The only open design question is the Muse Code hook protocol
(see "Verify the Muse Code protocol" below), and there the answer is to
check the real thing, not to pick a different design.

## Branch and base

- Branch: `tooling/hooks-deterministic-gates`
- Base: main `37b4952` (Merge pull request #62, bridge/watcher-push)
- The branch already holds: spec `specs/hooks/requirements.md`,
  test-writing prompt, failing tests `tests/test_hooks.py` +
  `tests/test_muse_hooks.py` (commit `076f82c`, 31 tests, all failing),
  test report `prompts/tooling-hooks-test-report.md` (commit `51c07e5`),
  and the updated spec naming `approved-plan.txt` (this step's commit).

Fetch origin FIRST, then rebase onto origin/main, and verify the base
commit is `37b4952` before you write anything. If the base is not
`37b4952`, stop and say so in the report.

## Task

Implement the four hooks from `specs/hooks/requirements.md` so that all
31 tests in `tests/test_hooks.py` and `tests/test_muse_hooks.py` pass.
Do not change what the tests assert. The tests are approved.

Hook scripts to write (all stdlib-only Python, no new dependency):
- `hooks/iris-check-banned-files.py` — hook 1, banned files (pre-commit)
- `hooks/iris-check-lint.py` — hook 2, fast lint + types (pre-commit)
- `hooks/iris-check-plan.py` — hook 3, unplanned files (pre-commit)
- `.muse/hooks/iris-pre-tool-use.py` — hook 4, Muse Code PreToolUse hook

Wiring to write:
- `.muse/hooks.json` — wire the PreToolUse event to
  `.muse/hooks/iris-pre-tool-use.py`.
- An installer for the three pre-commit hooks (a script such as
  `hooks/install.py`, or a Makefile target) that copies them into
  `.git/hooks/` of the working tree. The tests call the scripts from
  `hooks/` directly, so the installer is for real use, not for the tests.

The approved plan file is `approved-plan.txt` at the repo root, one
approved file path per line, relative to repo root. Hooks 3 and 4 read
it. Do not invent a different plan filename.

The ban lists, the five shell patterns, the exit-code contract (non-zero
blocks a commit; exit 2 denies a tool call), and the
`hookSpecificOutput` deny shape are all in the spec and the tests. Follow
them exactly.

## Test fix (folded into this step)

The test review found one fragility in `tests/test_muse_hooks.py`. Fix it
as part of this step.

`TestCleanToolCalls` currently writes `approved-plan.txt` to the real repo
root to set up its tests, then restores it in tearDown. If the test
crashes mid-run, it leaves that file in the repo. Change it to use a
temporary directory as the repo root, the way `TestUnplannedFileWrites`
already does (it passes `repo_root=self.tmpdir` to `_run_hook`).

Constraint: change only the setup and teardown. Do not change any
assertion, any payload, or any expected outcome. The tests must still
assert exactly what they assert now. Commit the test fix in the same
commit as the implementation, and name it in the report.

## Verify the Muse Code protocol

This is the one open item from the test report, and it is the most
important verification in this step. The payload shape and the exit-2
deny in the tests are sourced from Claude Code docs, because the Meta
Muse docs at dev.meta.ai were unreachable when the tests were written.
The shape is corroborated by third-party Muse Code docs, but it is not
verified against Meta's official docs or a live Muse session.

Do this, and report the result:
1. Read the official Muse Code hook documentation at dev.meta.ai if it
   is reachable. If it is not reachable, say so and name the source you
   used instead.
2. Confirm, from the docs, the PreToolUse payload field names
   (`tool_name`, `tool_input.command`, `tool_input.file_path`), the
   `hookSpecificOutput` deny shape, and the exit-2 deny contract.
3. If the real protocol differs from what the tests pin, say so in the
   report, name the exact difference, and adjust the hook script AND the
   tests to match the real protocol. Do not silently keep a wrong shape.
4. Run a live check if you can: install the hook, run a small
   `muse exec` session, attempt a banned command (for example
   `git add -A`), and confirm the hook denies it before it runs. If you
   cannot run a live session, say so and name what you could not check.
   Do not claim a live check you did not run.

If the protocol is confirmed as the tests pin it, say so in one line in
the report. If it differs, the difference and the fix are the headline
of the report.

## Verification

- Run `python -m pytest tests/test_hooks.py tests/test_muse_hooks.py -q`.
  All 31 tests must pass.
- Run the full suite `python -m pytest tests/ -q`. No regressions. If a
  test outside these two files fails, say so in the report and name it.
  Do not weaken or skip a failing test to make the suite green.
- Run `ruff check .`, `ruff format --check .`, and `pyright` on the code
  you wrote. All three must pass.
- The hooks are verified on Windows (the Dell), where commits happen.
  If you are running on Windows, install the pre-commit hooks with the
  installer and make one test commit to prove the block works. If you are
  not on Windows, say so and name what is still unverified.

## Rules

- Stage named files only. Never `git add -A`. Never `git commit -a`.
- Never commit `.exe`, `dist/`, `build/`, `*.db`, `.pytest_cache/`,
  `*.spec` (except the already-tracked `iris.spec`), or secrets.
- Keep verbose comments and docstrings. Do not strip them.
- A comment that excuses a workaround (TODO, HACK, FIXME, "temporary",
  "this works, don't touch it") is filed as a GitHub issue, not left in
  the code.
- Do not push. Commit locally and stop. The report goes in the same
  commit as the implementation.
- Do not change the spec. If the spec is wrong, say so in the report and
  stop. Do not rewrite the spec to match the code.

## Report

Write the report to `prompts/tooling-hooks-implementation-report.md`.
It must include:
- The base commit you verified, and the commits you made.
- The test results: the 31 hook tests, and the full suite.
- The Muse Code protocol verification result (confirmed, or the exact
  difference and the fix).
- The TestCleanToolCalls temp-dir fix, and confirmation no assertion
  changed.
- Anything you could not verify, and what is still open.
- The challenge-the-frame answer (one line if the spec is the best path).

Stop after the report. Do not open a pull request. Do not merge.
