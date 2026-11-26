# Watcher fetch before checkout follow-up, test review

## Commit and base

- Branch is `bridge/watcher-fetch-before-checkout`. Local test commit is `727a4dbf484ae92ba58cef46d33e0d0c9a53e150`. It changes only `tests/test_bridge_watcher.py`.
- The first Git operation after setting up the isolated clone was an origin fetch. GitHub was unreachable from the sandbox. I imported the cached refs from the workspace. The branch was at `113e53ef7ddec1889b6a471a9fd09c276bda0d67`. Its merge base with cached `origin/main` was `da1b0512f706318d5c91905e36bad225ab9af729`.
- The workspace's origin fetch completed during testing. Main was still `da1b0512f706318d5c91905e36bad225ab9af729`, so no rebase was needed. The task branch advanced to `fbdd00426075d048732ea219828175bbb76bfd2d`, which adds only the follow-up prompt. I applied the test commit to that tip in the clone. The prompt file is the only difference from the checkout used for the test runs.
- The commit is in `C:\Users\Quant\AppData\Local\Temp\iris-watcher-followup-dbfdb2bc030f45189635ad8856f95153`. No push. GitHub CI has no run for this commit.

## Tests for review

`run_once` calls `_prepare_branch_checkout` only when the current branch differs from the prompt branch. A prompt found through GitHub can therefore launch from a stale local commit when the prompt branch is already checked out.

| Test | What the test requires | What the current watcher did |
| --- | --- | --- |
| `test_already_on_branch_starts_run_at_fetched_head` | Start Codex on the fetched commit. Keep the branch checked out. Check the remote-tracking ref and the parent of the agent commit. | Codex started on the stale local commit. |
| `test_already_on_branch_fetch_failure_skips_and_poll_continues` | Delete the remote branch. Skip its prompt without marking it seen. Run the next branch in the same poll. | The watcher launched Codex for the deleted branch. |
| `test_already_on_diverged_branch_skips_and_preserves_work` | Make conflicting local and remote commits. Log a skip and preserve the local commit and worktree. | The watcher launched Codex on the diverged branch. |

`test_behind_branch_is_logged_not_rebased` now allows fetch and fast-forward to succeed. It injects a non-fast-forward push rejection. It checks the push log, the seen list, and a second poll. It checks that no checkout or rebase ran and that the watcher attempted one push. Other tests that use an empty directory for an already-checked-out branch now mock the Git preparation calls so they can still check their own behavior.

## Verification

| Check | Result |
| --- | --- |
| Watcher suite | 3 expected failures, 92 passed, 6 subtests passed. The switch-path tests, dirty tree skip, missing branch skip, baseline, and push handling passed. |
| Full suite on the base | 431 passed, 46 subtests passed. |
| Ruff | `ruff check .` passed. |
| Flake8 | The error gate passed in the test file. The advisory check reported the same 10 style diagnostics as the base. |
| Git and credentials | `git diff --check` passed. Zero credential pattern matches in the added lines. The commit contains one test file. |
| Demo scripts | `visualizer.py --seed-demo` and `report.py` ran against a temp database. The synthetic run had 6 attempts, 3 traces, 2 successes, and 3 retries. |

I checked Git state directly in temporary repositories. After fetching the newer commit, Git refused the existing `fast_forward_branch` helper on the checked-out branch and left `HEAD` at the stale commit. The follow-up test checks the commit the runner actually sees.

The three failures are ready for Charles to review. Implementation and its verification are pending.
