# Watcher fetch-before-checkout: follow-up tests (Codex)

## Approval

Charles approved the follow-up fix. The Step 2 fix covers the branch-switch path: when the watcher needs to checkout a different branch, it fetches and fast-forwards before switching. The already-on-branch path still bypasses fetch entirely. If a new prompt is pushed to the branch the watcher is already sitting on, the local branch goes stale, the run starts on a stale head, and the push fails non-fast-forward — the same bug, on the other path. This follow-up closes that gap. Proceed with tests only.

## Setup

Fetch origin FIRST, then checkout `bridge/watcher-fetch-before-checkout`. The branch is at `113e53e` on origin, on main `da1b051`. Verify the base before starting work; if `origin/main` has moved past `da1b051`, rebase onto it first and report the new base.

Challenge the frame: are you sure you want the rebase? A simpler way exists if the branch is already current — if `origin/main` has not moved, no rebase is needed. Rebase only if main moved, then proceed as written.

Read the approved plan, the tests, and the watcher code first: the plan in `prompts/bridge-watcher-fetch-before-checkout-prompt.md` on this branch, the `FetchBeforeCheckoutTest` class and the `PushAfterRunTest.test_behind_branch_is_logged_not_rebased` test in `tests/test_bridge_watcher.py`, and `tools/bridge_watcher.py` (especially `_prepare_branch_checkout` and `run_once`). Do not start writing tests until you have read all three.

Note: the workspace blocks writes to its Git metadata (the Codex sandbox cannot create `.git/index.lock`), so a rebase or commit in the main repo will fail with Permission denied. Work around it the way Step 1 and Step 2 did: do the git work in an isolated clone in the temp folder, and put the report in the main repo's `prompts/` directory. Do not push; the branch is pushed by hand.

Do not use `Activate.ps1` — PowerShell script execution is disabled in this environment and activation fails. Run tests with the venv Python directly (`.\.venv314\Scripts\python.exe -m pytest ...`) or via `cmd /c "call .venv314\Scripts\activate.bat && ..."`.

## Follow-up: write the failing tests

Write the failing tests for the already-on-branch fetch fix, in `tests/test_bridge_watcher.py` (new class or extend `FetchBeforeCheckoutTest`). Cases:

1. Already on the branch, remote has a newer commit: the watcher fetches and fast-forwards before running, and the run starts on the latest head. The checkout itself is still skipped (already on the branch), only the fetch and fast-forward are added.
2. Already on the branch, fetch fails: skip, no agent run, no crash, prompt unmarked, poll continues.
3. Already on the branch, diverged (fast-forward impossible): skip, no work lost, logged plainly.
4. Update `test_behind_branch_is_logged_not_rebased` in `PushAfterRunTest`: its `fetch.assert_not_called()` premise is now wrong, because the fix WILL fetch when already on the branch. Remove that assertion. Mock `fetch_branch` and `fast_forward_branch` to succeed, keep `checkout.assert_not_called()`, and keep the test's core — a non-fast-forward push failure is logged in plain words, the poll moves on, no rebase, no retry, the run stays marked seen. The test's purpose (push-failure handling) does not change, only the fetch premise.
5. Existing skips still hold (dirty tree, missing branch, baseline, and the switch-path tests from Step 1/Step 2).

Falsification shape: each test forces the stale-head or failure condition, not just the happy path.

Show the failing tests and STOP for review before implementing. Do not write the implementation yet. The test review is the next gate.

## Scope

Touch only `tests/test_bridge_watcher.py` for this step. No implementation code yet. No push (the branch is pushed by hand). Stage named files only, never `git add -A`, never `git commit -a`. Commit the tests locally and report the failing-test run to `prompts/bridge-watcher-fetch-before-checkout-followup-codex-report.md`. Then STOP.
