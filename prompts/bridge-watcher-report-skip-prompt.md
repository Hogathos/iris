# Bridge prompt: watcher skips a prompt whose report already exists

## Problem
A prompt run by hand never marks itself seen in the watcher's seen-list, so the watcher runs it again when it next polls. That happened Nov 24 with the teardown prompt: run by hand at 12:39 PM, run again by the watcher at 7:55 PM, doing the same work twice.

## Intention / done definition
The watcher never runs a prompt whose work is already done. A prompt is done when its report file exists on the same branch. The watcher marks it seen and skips it, with a plain-words log line, and never downloads or runs it.

## The fix
In `run_once` in `tools/bridge_watcher.py`, before running a prompt file, check whether its report exists in the branch listing the watcher already fetched. No extra API call; the listing already holds every file in the prompts dir.

- The report filename comes from the prompt filename: a prompt named `<name>-prompt.md` has its report at `<name>-report.md` on the same branch. A filename that does not end in `-prompt.md` has no report to check; run it as normal.
- If the report is in the listing, mark the prompt seen (save the seen-list), log the skip in plain words through the existing `_log_skip`, and do not download or run it. Record the skip in the returned report under a new `skipped_reported` list of (branch, name, reason) tuples, next to `skipped_checkouts`.
- Exclude reported prompts from the `pending` list too, so no checkout happens when the only pending prompt is already reported.
- Do not change the baseline path, the seen-list format, the checkout logic, or the run command.

## Tests first
Write the failing tests first, as their own commit, then the fix as its own commit. Add the tests to `tests/test_bridge_watcher.py`, in the existing `RunOnceTest` style (fake lister, downloader, runner; temp dir for state, log, downloads):

- A prompt whose report is in the listing is skipped: not downloaded, not run, marked seen, logged in plain words, and listed in `skipped_reported`.
- A prompt with no report in the listing runs as normal.
- A filename that does not end in `-prompt.md` is never report-checked; it runs as normal.
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
