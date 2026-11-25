# Bridge prompt: watcher only runs prompt files, and skips ones whose report exists

## Problem
The watcher runs every file in the prompts/ dir on a watched branch, not just prompt files, and it runs a prompt again if it was already run by hand. Three failures Nov 24: (1) the teardown prompt, run by hand at 12:39 PM, ran again by the watcher at 7:55 PM; (2) the watcher then ran the teardown report file as if it were a prompt; (3) it then ran old watcher prompt files that were never in the seen-list. The agent did what each file said, so the watcher kept launching runs.

## Intention / done definition
The watcher runs only genuine new prompt files, exactly once each. A file is runnable only if its name ends in `-prompt.md`. A prompt is done when its report file exists on the same branch. The watcher marks done prompts seen and skips them, with a plain-words log line, and never downloads or runs them or any non-prompt file.

## The fix
In `run_once` in `tools/bridge_watcher.py`:

- Only files whose name ends in `-prompt.md` are runnable. Any other file in the prompts dir (especially `*-report.md`) is skipped: never downloaded, never run, never logged as a failure. It is simply not a prompt. Apply the filter in both the `pending` computation and the run loop, so a non-prompt file never triggers a checkout either.
- For a runnable prompt, check whether its report exists in the branch listing the watcher already fetched. No extra API call; the listing already holds every file in the prompts dir. The report filename comes from the prompt filename: a prompt named `<name>-prompt.md` has its report at `<name>-report.md` on the same branch.
- If the report is in the listing, mark the prompt seen (save the seen-list), log the skip in plain words through the existing `_log_skip`, and do not download or run it. Record the skip in the returned report under a new `skipped_reported` list of (branch, name, reason) tuples, next to `skipped_checkouts`.
- Exclude reported prompts from the `pending` list too, so no checkout happens when the only pending prompt is already reported.
- Do not change the baseline path, the seen-list format, the checkout logic, or the run command.

## Tests first
Write the failing tests first, as their own commit, then the fix as its own commit. Add the tests to `tests/test_bridge_watcher.py`, in the existing `RunOnceTest` style (fake lister, downloader, runner; temp dir for state, log, downloads):

- A `*-report.md` file in the listing is never downloaded or run, never appears in `launched`, and never triggers a checkout.
- A file with no `-prompt.md` suffix is never downloaded or run.
- A prompt whose report is in the listing is skipped: not downloaded, not run, marked seen, logged in plain words, and listed in `skipped_reported`.
- A prompt with no report in the listing runs as normal.
- When the only pending prompt has a report, no checkout is attempted and the runner is never called; the prompt is marked seen.
- A second poll runs nothing for a prompt skipped this way (it is in the seen-list now).

## Verification
- The new tests fail on the tests-only commit and pass on the fix commit.
- The full suite stays green.
- Ruff and flake8 clean (the repo's selection: E9, F63, F7, F82).
- Exactly two files changed: `tools/bridge_watcher.py` and `tests/test_bridge_watcher.py`. No other files.

## Report
Write the report to `prompts/bridge-watcher-report-skip-report.md` on this branch and push it as its own commit, separate from the test and fix commits. The report states the commits, the test results (fail-before, pass-after, full suite count), what changed, and what stays open. Do not open a PR. Do not merge. Branch only.

## Frame check
The simpler alternative is editing the seen-list by hand after every by-hand run. That was offered and rejected: it is a manual step that will be forgotten. The durable fix is this one. No further frame challenge is needed; do the fix above.
