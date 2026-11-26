# Watcher fetch-before-checkout Step 1

## Base and commit

A rebase would have been redundant if the branch already contained current main. The fetched main was ahead and included the Codex routing merge, PR #64.

Fetched origin first. The workspace blocked writes to its Git metadata. Rebased the local branch from `13f86e2` in an isolated checkout. The verified main base is `da1b0512f706318d5c91905e36bad225ab9af729`. The rebased prompt commit is `c4355a2`.

Test commit `ffabcb9cd8c1b567a822aa60120f4d2137b9a39a` changes only `tests/test_bridge_watcher.py`. It is on `bridge/watcher-fetch-before-checkout` in `C:\Users\Quant\AppData\Local\Temp\iris-watcher-step1-debbd0b5`. The original workspace branch stays at `13f86e2`. The test commit is local.

In a shell with write access to the original Git metadata, fetch the test commit and its rebased parent into the original repository with this command.

```powershell
git fetch "C:\Users\Quant\AppData\Local\Temp\iris-watcher-step1-debbd0b5" refs/heads/bridge/watcher-fetch-before-checkout
```

## What the tests show

`run_once` in `tools/bridge_watcher.py` fetches only when the local branch is missing. With an existing branch, it calls `checkout_branch` while the refs can still point to an older commit.

| Test | Result on the current watcher |
| --- | --- |
| `test_existing_branch_is_updated_before_checkout_and_run` | Fails. Both refs still point to the older commit when checkout starts. The test also checks that the fetched prompt commit is the parent of the agent's commit. |
| `test_fetch_failure_skips_existing_branch_and_poll_continues` | Fails. The watcher calls the Codex runner when origin no longer has the branch. The test also requires the next branch in the same poll to run and only its prompt to be marked seen. |
| `test_diverged_branch_preserves_local_work_without_checkout` | Fails. The watcher calls checkout even though local and origin have separate commits. The test checks that the local commit and file contents survive and that the log explains the skip. |
| Dirty tree, missing branch after a failed fetch, and baseline | Pass. The existing test for a fetch that returns without supplying the branch also passes. |

The tests use local Git repositories. Git refs and commit parentage provide an independent check. The agent calls and push are mocked. The old test that required an existing local branch to skip fetch was replaced. Two checkout-only tests now mock a branch available through fetch so their checkout assertions continue to cover the intended paths.

## Verification

- Activated `.venv314` with `activate.bat` and used Python 3.14.10.
- Watcher suite on the test commit produced 3 expected failures, 89 passes, and 6 passing subtests.
- Ruff and the CI-selected Flake8 checks passed on the test file. The full Flake8 style check reported the same 10 diagnostics as the base file.
- The added lines had zero credential-pattern matches. `git diff --check` passed.
- The report and visualizer ran against seeded demo data. The visualizer found 6 attempts across 3 tasks, with 2 successful tasks and 3 retries. The seeded demo total was $0.0120.
- The full suite produced the same 3 expected failures, 428 passes, and 46 passing subtests. The commit was kept local, so CI has no run for it.

The tests are ready for Charles's review before Step 2.
