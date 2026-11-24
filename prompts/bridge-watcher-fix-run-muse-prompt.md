# Bridge watcher fix: run_muse gets a command, not a path, plus first-run baseline — fix prompt

Fix the bug that stops the watcher from running any prompt, and
fix the first-run backlog so it does not re-run old prompts. Read
the report first, then the code, then fix both. Add regression
tests that fail before the fix and pass after it. Do not edit the
existing tests. Make the new tests pass and the full suite green.

## Bug 1: run_muse gets a command, not a path

On the Dell, the watcher runs but every poll fails with:
`bridge watcher: poll failed (expected str, bytes or os.PathLike
object, not list), retrying`

Root cause, confirmed by Main by reproduction: `run_once` in
`tools/bridge_watcher.py` passes a command list to the runner
(`runner(build_command(dest))`). But `run_muse` expects a prompt
path string and calls `build_command` on it again, nesting the
list inside the command. `subprocess.run` then raises TypeError
because a command element is a list, not a string.

The tests pin the contract: the runner receives a command list.
`test_only_new_prompts_run_and_are_logged` asserts the runner
is called with `["muse", "exec", "--prompt-file", <path>,
"--disable-approval"]`. The tests mock the runner, so they never
caught that the real `run_muse` does not match that contract.
The bug only shows in live use, which is why the review missed
it. The oracle (a live run) would have caught it.

## Bug 2: first run re-runs every old prompt

On the Dell, the watcher downloaded `app-mode-connect-fix-prompt.md`
at 6:31 AM. That is an old Connect prompt from the PR #40 era,
already done and merged. It is in the prompts/ dir on the watched
branch, and the seen-list was empty, so the watcher treated it as
new. Only one file downloaded because Bug 1 aborted the poll after
the first file. Once Bug 1 is fixed, the watcher will try to run
every old prompt file in the prompts/ dir, one by one, re-doing
already-finished work.

Fix: on first run, when the seen-list file does not exist yet, the
watcher baselines. It lists the prompts, marks every file it finds
as seen WITHOUT running any of them, saves the seen-list, and logs
that it baselined. From then on, only files that appear after the
baseline run. A later poll that finds a file already in the
seen-list skips it, as today. The baseline runs once, on the first
poll only, when there is no seen-list file at all. If a seen-list
file exists (even an empty list), there is no baseline: the watcher
runs files not in the list, as today.

## Read

- `tools/bridge_watcher.py` — the watcher. The bug is in
  `run_muse` and how `run_once` calls it.
- `tests/test_bridge_watcher.py` — the existing tests. Do not
  edit them. The runner contract they pin (a command list) is
  correct and stays.
- `AGENTS.md` — the working rules.

## Do

1. Add a regression test in `tests/test_bridge_watcher.py` for
   Bug 1 that fails before the fix and passes after it. It must
   call the real `run_muse` with a command list (mock
   `subprocess.run` so it does not launch a real `muse`) and
   assert `subprocess.run` is called with that exact command
   list, and that the exit code is returned. It must fail on the
   current code (TypeError) and pass after the fix.
2. Fix `run_muse` (Bug 1) so it accepts a command list and runs
   it directly with `subprocess.run`, returning the exit code.
   Update its docstring to say it takes a command, not a prompt
   path. Do not change `run_once` or `build_command` for Bug 1:
   they are correct and the tests pin them.
3. Add a regression test in `tests/test_bridge_watcher.py` for
   Bug 2 that fails before the fix and passes after it. It must
   run `run_once` with no seen-list file present and a listing
   of two prompt files, and assert that neither file runs, that
   both are in the seen-list after, and that the second poll with
   the same listing runs nothing. It must also assert that a
   third file appearing in a later poll DOES run. It must fail on
   the current code (both files run on the first poll) and pass
   after the fix.
4. Fix the baseline (Bug 2) in `run_once`: when the seen-list
   file does not exist yet, list the prompts, mark every file
   found as seen WITHOUT running any of them, save the seen-list,
   and log that it baselined. From then on, only files that appear
   after the baseline run. If a seen-list file exists (even an
   empty list), there is no baseline.
5. Run the new regression tests. Each must fail before its fix
   and pass after it. Show both.
6. Run the full suite (`python -m pytest tests/ -q`) plus
   `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`, and pyright.
   All must pass.
7. Run the oracle, a live run on this machine: `python
   tools/bridge_watcher.py --once` with the config pointing at
   a branch that has prompt files, and confirm it baselines
   (marks them seen, runs none) instead of the TypeError, and
   that a second `--once` run does nothing. Report the result.
8. Report the commit hash, the test results, the oracle result,
   and what is still open. Then stop.

## Rules

- Do not edit the existing tests. Add only the new regression
  tests.
- The baseline (Bug 2) must not run any prompt on first run. It
  marks what exists as seen and only runs what appears after.
  A file that appears after the baseline runs exactly once.
- Commit named files only. Never `git add -A`, never
  `git commit -a`. Never commit `.exe`, `dist/`, `build/`,
  `*.db`, `.pytest_cache/`, or secrets.
- Push the branch only. Do NOT open a PR or merge.
- Do not report session usage; the usage panel is not readable
  from inside the session, Charles screenshots it.
