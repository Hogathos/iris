# Watcher fetch before checkout follow-up implementation

## Git

- Local commit `0555fb6b45354c37d3684b831e535fdfc17700ed` changes `tools/bridge_watcher.py` only. The test file matches the approved test commit `727a4dbf484ae92ba58cef46d33e0d0c9a53e150`.
- I attempted the origin fetch before checkout. GitHub was unreachable from this session. I ran the stashed baseline on the initial cached tip `727a4db`.
- The workspace's origin refs refreshed during verification. Main and the branch merge base both matched `da1b0512f706318d5c91905e36bad225ab9af729`, so I skipped the rebase. The refreshed branch included the report `a6d8f74` and the later implementation prompt `e67a788`. I placed the implementation on top of `e67a788`. Git confirmed that only those two prompt files differ between the approved test commit and the refreshed branch tip.
- The implementation commit is local to `C:\Users\Quant\AppData\Local\Temp\iris-watcher-followup-step2-bf819531ddf943e5af5bcdfdd1aa9675` on `bridge/watcher-fetch-before-checkout`. Push status is local only.

## Behavior

Before running pending prompts, the watcher fetches the target branch. If the branch is already checked out, Git checks that `HEAD` is an ancestor of the fetched commit, then advances the branch and working tree with `git merge --ff-only`. The ancestry check also rejects a local branch ahead of origin. Git protects overlapping local files, including ignored files. The watcher skips checkout on this path.

A fetch failure or a refused fast-forward logs a skip and leaves the prompt unseen. The poll continues with the next branch. Branches that need checkout still use the existing local fetch for the fast-forward.

## Verification

| Check | Result |
| --- | --- |
| Watcher suite | 95 passed, 6 subtests passed. The three new tests and the updated push rejection test passed as written. |
| Full suite with the implementation stashed | 431 passed, 46 subtests passed. Only `test_already_on_branch_starts_run_at_fetched_head`, `test_already_on_branch_fetch_failure_skips_and_poll_continues`, and `test_already_on_diverged_branch_skips_and_preserves_work` failed. |
| Full suite on `0555fb6` | 434 passed, 46 subtests passed. |
| Lint | `ruff check .` and the flake8 `E9,F63,F7,F82` gate passed. Ruff advisory totals matched the baseline. The watcher file still had 5 CI-style and 10 default flake8 diagnostics, with the same codes and messages. |
| Diff and credentials | `git diff --check` passed. The scan found zero credential pattern matches in 42 added lines. |
| Demo scripts | `visualizer.py` and `report.py` ran on a temporary database. The synthetic demo had 6 attempts, 3 traces, 2 successes, and 3 retries. |

## Independent Git read-back

The read-back used real local repositories and called the watcher preparation path with the target branch already checked out.

| Condition | Git and working tree result |
| --- | --- |
| Stale branch | `HEAD` advanced from `486926b` to `e164352`. The fetched tracking ref also read `e164352`, and the working file contained the fetched text. The branch stayed checked out. |
| Missing origin branch | The watcher returned a fetch skip. `HEAD` and the cached tracking ref both stayed at `e5263ad`. The approved poll test also confirmed that the next branch ran. |
| Diverged branch | The watcher returned a fast-forward skip. `HEAD` stayed at `e012f60` and the fetched tracking ref advanced to `7e31b88`. The local file kept its text, and Git reported a clean working tree. |
| Local branch ahead or overlapping local files | The watcher skipped the run. Git kept the ahead commit, the overlapping uncommitted edit, and the overlapping ignored file in their respective fixtures. |

The review found one fetch per branch with pending work. The checked-out path adds an ancestry check and a local merge. It keeps Git's working tree checks in effect. GitHub Actions for the local commit, concurrent manual Git changes during a poll, and a live remote poll remain unverified.
