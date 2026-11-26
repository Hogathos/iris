# Watcher: fetch branch before checkout for a run

## Base and frame challenge

Fetch origin FIRST. Rebase onto `origin/main` if it has moved since your last fetch, and report the base commit you built on. Do not assume the base. Verify it and report it.

Challenge the frame before you start: if a simpler or sturdier way to stop the repeated non-fast-forward push rejections exists than the fix below, say so once, up front, in your report, then do the fix as written. Do not nag the point. Silence means you checked and this is the best path.

## The problem

The watcher discovers new prompts through the GitHub API, but it checks out the branch for a run from its local repo, which it has not fetched. So the run starts on a stale head. Muse Code does the work on top of that stale head. The prompt commit that was just pushed to the remote is never in the local history.

Then the push is rejected as non-fast-forward, because the remote has the prompt commit and the local branch does not. The user has to rebase by hand every time. This has happened on `app-mode/implementation` twice now (the CI fix prompt and the CI fix 2 prompt). It is obnoxious and it is a watcher bug.

## The fix

Before checking out a branch for a run, fetch that branch from origin and update the local branch to the remote head, so the run starts on the latest head, including the prompt commit.

Concretely, in the run path (where the watcher checks out the branch and launches `muse exec`):
- Fetch the branch from origin first (`git fetch origin <branch>` or equivalent). If the fetch fails, log the failure plainly and skip the run. Do not run on a stale base. A failed fetch must never crash the poll and must never mark the prompt as seen.
- After a successful fetch, update the local branch to the remote head before checking it out (fast-forward only; never force, never rebase the user's local branch here — if the local branch has diverged from the remote in a way a fast-forward cannot fix, log it and skip the run, do not lose work).
- The existing skip rules stay: if the tree has uncommitted changes that a checkout would lose, skip the run (do not lose work). If there is no local branch and no fetch can get it, skip (do not fetch the whole remote as a workaround; the existing rule stands).

Do not change the push-after-run behaviour (PR #62, `push_branch`). This is a separate fix. If PR #62 has merged by the time this runs, rebase onto the new main and keep the push code intact. Do not break push. Do not touch the seen list format or the config format.

## Tests

Test-first. Write failing tests first, then the implementation. Cover:
- A run starts on the latest remote head: the local branch is updated to the remote head before checkout, and the prompt commit is an ancestor of the run's commits.
- A fetch failure skips the run: no `muse exec` launch, no crash, the prompt is not marked seen, the poll continues.
- A diverged local branch (fast-forward impossible) skips the run: no checkout, no work lost, logged plainly.
- The existing skips still hold: uncommitted changes skip, missing branch skips, baseline pushes nothing.

## What not to do

- Do not merge any branch. Do not rebase the user's local branch as part of a run (the fetch-and-fast-forward above is the only history move, and only when it is a clean fast-forward).
- Do not force-push, do not fetch the whole remote to work around a missing branch.
- Do not push. The sandbox blocks the push. Commit locally and report. The branch is pushed by hand.
- Do not merge. Do not open a PR. This is a fix on its own branch.
- Touch only `tools/bridge_watcher.py` and `tests/test_bridge_watcher.py`. No spec file, per the watcher-fix precedent. The report file is the record.

## Verification

- Watcher file tests pass (old + new).
- Full suite green on the Dell (the socket tests fail there at bind, WinError 10013 — that is environmental, not a regression; report the counts and confirm the failure set is identical with the change stashed).
- `ruff check .` clean. `flake8` clean on the touched files.
- Falsification: the new tests must attack the fix (a run that must not start on a stale head, a fetch failure that must not crash or mark seen, a diverged branch that must not lose work), not just confirm the happy path.

## Report

Write your report to `prompts/bridge-watcher-fetch-before-checkout-report.md`, own commit. It must state: the base commit you built on; the frame challenge (what you checked, one line); the root cause as you found it in the code (which function checks out the branch, and where the fetch is missing); what the fix does; the test results (watcher file, full suite with stashed baseline); ruff and flake8 results; and what is still unknown.
