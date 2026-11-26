# Watcher fetch-before-checkout: follow-up implementation (Codex)

## Approval

Charles approved the follow-up tests. Implement the fix. The tests are the spec.

## Setup

Fetch origin FIRST, then checkout `bridge/watcher-fetch-before-checkout`. The branch is at `a6d8f74` on origin, on main `da1b051`. The approved tests are commit `727a4db` ("Test watcher refresh for the checked-out branch"), and the test report is `a6d8f74`. Verify the base before starting work; if `origin/main` has moved past `da1b051`, rebase onto it first and report the new base.

Challenge the frame: are you sure you want the rebase? A simpler way exists if the branch is already current — if `origin/main` has not moved, no rebase is needed. Rebase only if main moved, then proceed as written.

Read the approved tests and the watcher code first: the 3 new tests in `FetchBeforeCheckoutTest` (`test_already_on_branch_starts_run_at_fetched_head`, `test_already_on_branch_fetch_failure_skips_and_poll_continues`, `test_already_on_diverged_branch_skips_and_preserves_work`) and the updated `test_behind_branch_is_logged_not_rebased` in `tests/test_bridge_watcher.py`, and `tools/bridge_watcher.py` (especially `_prepare_branch_checkout`, `fetch_branch`, `fast_forward_branch`, and `run_once`). Do not start implementing until you have read the tests.

Note: the workspace blocks writes to its Git metadata (the Codex sandbox cannot create `.git/index.lock`), so a rebase or commit in the main repo will fail with Permission denied. Work around it the way Step 1, Step 2, and the follow-up tests did: do the git work in an isolated clone in the temp folder, and put the report in the main repo's `prompts/` directory. Do not push; the branch is pushed by hand.

Do not use `Activate.ps1` — PowerShell script execution is disabled in this environment and activation fails. Run tests with the venv Python directly (`.\.venv314\Scripts\python.exe -m pytest ...`) or via `cmd /c "call .venv314\Scripts\activate.bat && ..."`.

## The fix

`run_once` calls `_prepare_branch_checkout` only when the current branch differs from the prompt branch. When the prompt branch is already checked out, the watcher never fetches, so a run can start on a stale head.

On the already-on-branch path, fetch the branch and advance it to the fetched head before running, using the same skip rules as the switch path: a fetch failure or a refused fast-forward logs a plain skip, leaves the prompt unseen, and the poll continues. The checkout itself stays skipped (already on the branch).

Key finding from the test run, do not ignore it: Git refuses the existing `fast_forward_branch` helper on a checked-out branch and leaves `HEAD` at the stale commit. That helper fetches from the repo itself (`git fetch . <refspec>`), which Git will not do for the branch currently checked out. The implementation needs a different mechanism for advancing the branch that is currently checked out — one that Git permits for the checked-out branch and that still refuses a non-fast-forward update. Fast-forward only; never force, never rebase, never lose local work. If the mechanism you choose cannot prove fast-forward-only, say so in the report and do not ship it.

The tests are the spec and must not be modified unless genuinely buggy. If a test is genuinely buggy, say so in the report and explain why. Do not adjust a test to fit the implementation.

## Scope

Touch only `tools/bridge_watcher.py`. Do not modify the tests. No push (the branch is pushed by hand). Stage named files only, never `git add -A`, never `git commit -a`. Commit the implementation locally and report to `prompts/bridge-watcher-fetch-before-checkout-followup-step2-codex-report.md`. Then STOP.

## Verification

- Watcher suite green (all 3 new tests pass as written, plus the existing suite).
- Full suite green, with a stashed-baseline comparison proving the only failures before the fix are the 3 approved tests.
- `ruff check .` clean. Flake8 gate (E9,F63,F7,F82) clean on the touched files. Style diagnostics unchanged from baseline.
- `git diff --check` clean. No credential patterns in added lines.
- Falsification: verify the fix against the conditions the tests force (stale head, fetch failure, diverged branch), not just the happy path. Include a direct Git read-back (real repos, not mocks) proving the checked-out branch advances to the fetched head and that a diverged branch is preserved.
- Report the base commit, what the fix does, the test results, and what is still unknown.
