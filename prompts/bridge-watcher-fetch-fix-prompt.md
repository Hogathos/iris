# Bridge prompt: watcher fetches a branch that exists on GitHub but not locally

## Problem
The watcher skips a prompt when its branch exists on GitHub but not locally on the Dell, with `no local branch <branch>, no fetch attempted, run skipped`. This happened Nov 24 9:10 PM with `bridge/watcher-report-skip`: the prompt was filed and the config was right, but the Dell had never checked the branch out, so the watcher refused to run it. The fix required a manual `git fetch origin` and `git checkout` on the Dell before the watcher could do its job. That breaks the automation: every new branch needs a hand step first.

## Intention / done definition
The watcher fetches a branch from GitHub when the local branch is missing, then checks it out and runs the prompt. A fetch failure is a plain skip, logged in plain words, not a crash. A branch that does not exist even after a fetch is still skipped as before. The watcher never runs a prompt twice and never loses uncommitted changes; this fix touches only the missing-branch path.

## The fix
In `run_once` in `tools/bridge_watcher.py`, in the block that currently skips with `no local branch {branch}, no fetch attempted`:

- When `branch_exists_locally` returns False, run `git fetch origin <branch>` first (fetch just that branch, not the whole remote). `git fetch` only downloads objects and updates the remote-tracking ref; it does not touch the working tree and does not move any local branch, so it is safe to run before the dirty-tree check has passed. It runs after the dirty check in the current code order, so uncommitted changes are still protected.
- If the fetch fails (non-zero exit, network error, any exception), log a plain skip through the existing `_log_skip` with a reason naming the fetch failure, record it in `skipped_checkouts`, and move on. Do not crash, do not retry in the same poll.
- If the fetch succeeds, re-check for the branch. A branch that exists as a remote-tracking branch after the fetch is enough: `git checkout <branch>` creates the local tracking branch automatically when no local branch exists but `origin/<branch>` does. Proceed to the existing `checkout_branch` call.
- If the branch still does not exist after a successful fetch, skip as before with the existing reason.
- Update the `branch_exists_locally` docstring: it currently says "No fetch: the watcher has no credentials for that." That is wrong for this repo. The repo is public and fetch works without credentials on the Dell (git fetch already works unauthenticated for it). Correct the docstring to describe what the function actually checks.
- Do not change the baseline path, the seen-list format, the dirty-tree check, the checkout logic, or the run command. Fetch happens only on the missing-branch path, not on every poll.

## Tests first
Write the failing tests first, as their own commit, then the fix as its own commit. Add the tests to `tests/test_bridge_watcher.py`, in the existing `RunOnceTest` style (fake lister, downloader, runner; temp dir for state, log, downloads; fake the git calls the same way the existing checkout tests do):

- A prompt on a branch with no local branch but a fetchable remote branch is fetched, checked out, and run.
- A fetch failure is a plain skip: not run, not marked seen, logged in plain words, and listed in `skipped_checkouts`.
- A branch that does not exist even after a successful fetch is still skipped as before.
- A prompt on a branch that already exists locally is not fetched (no extra git call) and runs as normal.
- A second poll runs nothing for a prompt fetched and run this way (it is in the seen-list now).

## Verification
- The new tests fail on the tests-only commit and pass on the fix commit.
- The full suite stays green.
- Ruff and flake8 clean (the repo's selection: E9, F63, F7, F82).
- Exactly two files changed: `tools/bridge_watcher.py` and `tests/test_bridge_watcher.py`. No other files.

## Report
Write the report to `prompts/bridge-watcher-fetch-fix-report.md` on this branch and push it as its own commit, separate from the test and fix commits. The report states the commits, the test results (fail-before, pass-after, full suite count), what changed, and what stays open. Do not open a PR. Do not merge. Branch only.

## Frame check
The simpler alternative is the manual step: fetch and check out each new branch on the Dell by hand before the watcher can run it. That was the status quo and it broke the automation on Nov 24. The durable fix is this one. No further frame challenge is needed; do the fix above.
