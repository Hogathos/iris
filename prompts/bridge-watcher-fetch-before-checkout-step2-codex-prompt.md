# Watcher fetch-before-checkout: Step 2 implementation (Codex)

## Approval

Charles approved the Step 1 tests (branch `bridge/watcher-fetch-before-checkout`, commit `ffabcb9`, on top of main `da1b051`). The 6 new tests in `FetchBeforeCheckoutTest` are approved: 3 fail (the new behavior), 3 pass (preserved behavior). Full suite on the test commit: 3 failed, 428 passed, 46 subtests passed. Proceed with Step 2 only: implement the fix so the 3 failing tests pass.

## Setup

Fetch origin FIRST, then checkout `bridge/watcher-fetch-before-checkout`. The branch is at `ffabcb9`, already on current main `da1b051`. Verify the base before starting work; if `origin/main` has moved past `da1b051`, rebase onto it first and report the new base.

Challenge the frame: are you sure you want the rebase? A simpler way exists if the branch is already current — if `origin/main` has not moved, no rebase is needed. Rebase only if main moved, then proceed as written.

Read the approved plan, the tests, and the watcher code first: the plan in `prompts/bridge-watcher-fetch-before-checkout-prompt.md` on this branch, the `FetchBeforeCheckoutTest` class in `tests/test_bridge_watcher.py` (the tests are the spec — do not modify them unless one has a genuine bug, and if so say so in the report), and `tools/bridge_watcher.py`. Do not start implementing until you have read all three.

Note: the workspace blocks writes to its Git metadata (the Codex sandbox cannot create `.git/index.lock`), so a rebase or commit in the main repo will fail with Permission denied. Work around it the way Step 1 did: do the git work in an isolated clone in the temp folder, and put the report in the main repo's `prompts/` directory. Do not push; the branch is pushed by hand.

Do not use `Activate.ps1` — PowerShell script execution is disabled in this environment and activation fails. Run tests with the venv Python directly (`.\.venv314\Scripts\python.exe -m pytest ...`) or via `cmd /c "call .venv314\Scripts\activate.bat && ..."`.

## Step 2: implement the fix

Implement the fetch-before-checkout fix in `tools/bridge_watcher.py`, per the approved plan, so the 3 failing tests pass:

1. `test_existing_branch_is_updated_before_checkout_and_run` — in the run path, fetch the branch from origin before checkout, then fast-forward the local branch to the remote head (fast-forward only; never force, never rebase the user's local branch here). The run must start on the latest head, and the fetched prompt commit must be an ancestor of the run's commits.
2. `test_fetch_failure_skips_existing_branch_and_poll_continues` — if the fetch fails, log the failure plainly and skip the run. No agent launch, no crash, the prompt is not marked seen, the poll continues, and the next branch in the same poll still runs.
3. `test_diverged_branch_preserves_local_work_without_checkout` — if the local branch has diverged from the remote in a way a fast-forward cannot fix, log it plainly and skip the run. No checkout, no work lost, the local commit and file contents survive.

Existing skips stay: uncommitted changes skip, missing branch skips, baseline pushes nothing. Do not change the push-after-run behaviour (`push_branch`). Do not touch the seen-list format or the config format. Do not merge any branch. Do not force-push, do not fetch the whole remote to work around a missing branch.

Falsification: the implementation must make the 3 failing tests pass without weakening them. If a test cannot pass as written, say so in the report and stop — do not silently change the test to fit the implementation.

## Verification

- Watcher file tests pass (old + new, all green).
- Full suite green on the Dell (the socket tests fail there at bind, WinError 10013 — that is environmental, not a regression; report the counts and confirm the failure set is identical with the change stashed).
- `ruff check tests/test_bridge_watcher.py` clean (and `ruff check .` if quick). Flake8 gate clean on the touched files (`--select=E9,F63,F7,F82`); the full flake8 style check has pre-existing diagnostics in this file — report them and confirm none are new.
- `git diff --check` clean. No credential patterns in added lines.

## Scope

Touch only `tools/bridge_watcher.py` (and `tests/test_bridge_watcher.py` only if a test has a genuine bug — say so in the report). No push (the branch is pushed by hand). Stage named files only, never `git add -A`, never `git commit -a`. Commit the implementation locally and report to `prompts/bridge-watcher-fetch-before-checkout-step2-codex-report.md`. Then STOP.
