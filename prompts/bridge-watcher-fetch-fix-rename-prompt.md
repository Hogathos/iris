# Bridge prompt: rename the fetch-test prompt filenames to prompt files

## Problem

The fetch-fix branch is rebased onto current main (`6789bfb`), which has the report-skip fix. The five `FetchMissingBranchTest` tests in `tests/test_bridge_watcher.py` use `"new.md"` as the prompt filename. On current main, the watcher only runs `*-prompt.md` files, so `"new.md"` is skipped and nothing runs. The reviewer merged the branch into current main and ran the watcher tests: all 5 new fetch tests fail for this reason. The rebase is done. Only the filenames need fixing.

## Intention / done definition

The five fetch tests use a prompt filename, and the watcher tests pass on this branch. The fetch fix itself does not change. No new behavior, no new files beyond this fix.

## The fix

1. In `tests/test_bridge_watcher.py`, in the five `FetchMissingBranchTest` tests, rename the prompt filename from `"new.md"` to `"new-prompt.md"` everywhere it appears (the lister, the expected launched entries, and the seen-list assertions). Only the filename changes. No test logic changes.
2. Run the watcher tests: `pytest tests/test_bridge_watcher.py -q`. All tests must pass, including the 5 fetch tests and the 46 report-skip tests.
3. Run the full suite and the lints: `pytest tests/ -q`, `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`. The full suite must stay green. Ruff and flake8 must be clean.
4. Commit the rename as its own commit and push the branch. Do not force-push; the rebase is already done, this is a normal commit on top.
5. Do not open a PR. Do not merge. Branch only.

## Verification

- The 5 fetch tests pass on this branch.
- The full watcher file passes (51 tests: 46 report-skip + 5 fetch).
- The full suite stays green.
- Ruff and flake8 clean.
- Only `tests/test_bridge_watcher.py` changed in this commit. No other files.

## Report

Write the report to `prompts/bridge-watcher-fetch-fix-rename-report.md` on this branch and push it as its own commit, separate from the rename commit. The report states the commit, the test results, what changed, and what stays open. Do not open a PR. Do not merge. Branch only.

## Frame check

The simpler alternative is to leave the filenames as `"new.md"` and change the filter instead. That would break the report-skip fix, which is the point of the filter. The filenames are the fix. No further frame challenge is needed; do the rename above.
