# Bridge watcher fix: run_muse gets a command, not a path — fix prompt

Fix the bug that stops the watcher from running any prompt. Read
the report first, then the code, then fix it. Add a regression
test that fails before the fix and passes after it. Do not edit
the existing tests. Make the new test pass and the full suite
green.

## The bug

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

## Read

- `tools/bridge_watcher.py` — the watcher. The bug is in
  `run_muse` and how `run_once` calls it.
- `tests/test_bridge_watcher.py` — the existing tests. Do not
  edit them. The runner contract they pin (a command list) is
  correct and stays.
- `AGENTS.md` — the working rules.

## Do

1. Add a regression test in `tests/test_bridge_watcher.py` that
   fails before the fix and passes after it. It must call the
   real `run_muse` with a command list (mock `subprocess.run`
   so it does not launch a real `muse`) and assert `subprocess.run`
   is called with that exact command list, and that the exit code
   is returned. It must fail on the current code (TypeError) and
   pass after the fix.
2. Fix `run_muse` so it accepts a command list and runs it
   directly with `subprocess.run`, returning the exit code.
   Update its docstring to say it takes a command, not a prompt
   path. Do not change `run_once` or `build_command`: they are
   correct and the tests pin them.
3. Run the new regression test. It must fail before the fix and
   pass after it. Show both.
4. Run the full suite (`python -m pytest tests/ -q`) plus
   `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`, and pyright.
   All must pass.
5. Run the oracle, a live run on this machine: `python
   tools/bridge_watcher.py --once` with the config pointing at
   a branch that has a prompt file, and confirm it launches
   Muse Code (or logs the launch) instead of the TypeError.
   Report the result.
6. Report the commit hash, the test results, the oracle result,
   and what is still open. Then stop.

## Rules

- Do not edit the existing tests. Add only the new regression
  test.
- Commit named files only. Never `git add -A`, never
  `git commit -a`. Never commit `.exe`, `dist/`, `build/`,
  `*.db`, `.pytest_cache/`, or secrets.
- Push the branch only. Do NOT open a PR or merge.
- Do not report session usage; the usage panel is not readable
  from inside the session, Charles screenshots it.
