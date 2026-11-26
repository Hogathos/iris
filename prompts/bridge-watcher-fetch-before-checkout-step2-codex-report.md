# Watcher fetch before checkout, Step 2

## Commit

- Branch is `bridge/watcher-fetch-before-checkout`.
- Main was `da1b0512f706318d5c91905e36bad225ab9af729` after the first fetch. It was already the merge base, so a rebase would add no benefit.
- The fetched task branch had one commit after the approved test commit `ffabcb9`. Commit `c981638` adds the Step 2 prompt. I fast-forwarded the isolated clone to that commit before editing.
- Implementation commit is `2b9bc2838d1f86779a68a4dd268ee16738826ba4`. It changes only `tools/bridge_watcher.py`. The tests match `ffabcb9` byte for byte.
- Clone is at `C:\Users\Quant\AppData\Local\Temp\iris-watcher-step2-d063b86a`. The workspace fetch succeeded. When the clone's direct fetch could not reach GitHub, I imported the two fetched refs from the workspace.
- The commit is local. There was no push or merge. GitHub CI did not run for this commit.
- At the final workspace check, the remote task branch had advanced to `7061e1e`, which adds the Step 1 report. The local implementation and that report commit both descend from `c981638`. The manual push needs to include both commits.

## Cause and fix

`run_once` fetched only when the target branch was missing locally. The API could find a new prompt while the local checkout still pointed to an older commit. The agent then started behind the remote branch.

Before switching branches, the watcher now validates the branch name and fetches just that branch from origin. It asks Git to fast-forward an existing local branch from the fetched ref before checkout. Git refuses a non-fast-forward update and protects branches checked out in other worktrees. If the target branch exists only on origin, checkout still creates the tracking branch. A fetch or fast-forward failure logs a skip, leaves the prompt unseen, and allows the poll to reach the next branch.

The approved behavior for a branch already checked out still bypasses fetch and checkout. This fix covers branch switches.

## Verification

| Check | Result |
| --- | --- |
| Watcher suite | 92 passed, 6 subtests passed. All six approved tests passed as written. |
| Full suite with implementation | 431 passed, 46 subtests passed. |
| Full suite baseline | 428 passed, 3 failed, 46 subtests passed. The only failures were the three approved regressions. The baseline ran on the clean checkout and completed with the implementation stashed. |
| Failure comparison | The environmental failure set was empty in both runs. Neither run had a socket bind failure or `WinError 10013`. |
| Ruff | `ruff check tests/test_bridge_watcher.py` and `ruff check .` passed. |
| Flake8 gate | `E9,F63,F7,F82` passed on both watcher files. |
| Flake8 style | Default style gave the same 49 diagnostics. The watcher had 2 E127 and 8 E501. The test file had 1 E402, 5 E125, 4 E128, and 29 E501. CI style gave the same 15 diagnostics, with the existing `run_once` complexity count reduced from 42 to 35. |
| Git and credentials | `git diff --check` passed. The scan found zero credential pattern matches in the added source lines. The implementation commit contains only the intended file. |
| Demo scripts | `visualizer.py --seed-demo` and `report.py` ran against a temp database. Synthetic data yielded 6 attempts, 3 traces, 2 successes, and 3 retries. |

I checked the Git refs and working files directly in temporary repositories. A clone configured to fetch only main could still switch to the updated target branch. The watcher preserved the target branch and its files when a linked worktree held it. An invalid refspec was refused before fetch. The approved tests exercised a stale branch, a deleted remote branch, a later branch in the same poll, and diverged local work.

The unattended watcher on the Dell and a live agent push after a run remain unverified. A separate process switching branches between watcher Git commands was outside this check.
