# Bridge watcher report-skip fix: report

## What was wrong

The watcher ran every file in the prompts dir, not just prompt files.
It also re-ran a prompt that was already done by hand. Three failures
on Nov 24 showed it: the teardown prompt ran twice, its report file
ran as if it were a prompt, and old prompt files ran again.

## What changed

`run_once` in `tools/bridge_watcher.py` now treats only a file ending
in `-prompt.md` as runnable. Any other file is simply not a prompt:
it is never downloaded, never run, never logged as a failure, and it
never triggers a checkout.

For a runnable prompt, `run_once` checks the branch listing it already
fetched. A prompt named `<name>-prompt.md` is done when
`<name>-report.md` is in that same listing. No extra API call happens.
A done prompt is marked seen, skipped with a plain-words log line, and
recorded in a new `skipped_reported` list in the returned report. It is
also left out of the `pending` list, so no checkout happens for it.

Two small helpers carry this: `is_prompt_file` and `report_name`.
The baseline path, the seen-list format, the checkout logic, and the
run command are unchanged.

## Tests

New tests live in `tests/test_bridge_watcher.py` as
`PromptReportSkipTest`, in the existing `RunOnceTest` style. They cover:
a report file never runs and never triggers a checkout, a non-prompt
file never runs, a reported prompt is skipped and marked seen, an
unreported prompt runs as normal, a reported-only poll skips the
checkout, and a second poll runs nothing.

Old tests in the same file used bare `.md` names such as `new.md`.
Those names are not prompts under the new contract, so the old tests
were moved to `-prompt.md` names with the same assertions. This move
is part of the fix commit, in the same test file.

## Commits on branch `bridge/watcher-report-skip`

- `bac12e5` Add failing report-skip tests for bridge watcher
  (tests only).
- `e2467ac` Watcher runs only `*-prompt.md`, skips prompts whose
  report exists (fix plus old-test renames).

## Results

- New tests on the tests-only commit: 6 failed, as expected.
- Watcher file on the fix commit: 46 passed.
- Full suite: `tests/test_bridge_watcher.py` passes 46 of 46. The rest
  of the suite shows 68 failed and 74 errors, all in files that never
  import the watcher. They fail with socket `PermissionError`
  (WinError 10013): this sandbox forbids listening sockets. They are
  environmental and unrelated to this change.
- Ruff and flake8 (selection E9, F63, F7, F82) are clean on both
  changed files.

## Files changed

- `tools/bridge_watcher.py`
- `tests/test_bridge_watcher.py`
- This report: `prompts/bridge-watcher-report-skip-report.md`

## What stays open

- The full suite stays red outside the watcher file until it runs
  somewhere sockets are allowed. That is an environment limit, not a
  code gap from this fix.
- No pull request was opened and nothing was merged, as ordered.
  Branch only.
