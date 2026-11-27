# Bridge watcher Windows test race

PR #67 reported the failure at `f5eead6`. The local branch was at `39e88f0`, which adds the task prompt on top of that commit.

## Cause

The fake agent commits the report before it exits. The watcher runs the timed publisher on a separate thread and signals it to stop after `child.wait()` returns. A timed snapshot can commit after the report commit, while the child is still running. The watcher's `git commit --only` carries forward the report from its parent tree. The old `ls-tree` assertion inspected that tree, so it rejected a status commit that changed only the status file.

A local Git check on Windows confirmed the tree and the changed paths. A separate Codex test run held the child open until a live status commit inherited its report at the bare origin. The old assertion failed. The revised assertion passed with the same order of commits.

The first group of repeat runs also exposed Git index contention. The fake Muse agent's `git add` hit an existing `index.lock` while the timer was active. The child exited with code 1 before it could commit the report.

## Change

`tests/test_bridge_watcher.py` now checks the report path's history at the bare origin. Exactly one commit must change the report, and its subject must be `Add agent report`. This check accepts a live snapshot on either side of that commit. The fake agent retries Git writes for up to five seconds when Git reports an existing index lock. A forced lock at the report add verified that the retry recovers and the Codex test still passes.

The existing assertions check the live running snapshots and their 50-line tail limit, the final state for each exit code, the report contents at the origin, and the unrelated file in the local index.

## Local Windows verification

Tests ran with Python 3.14.10, pytest 9.2.0, and Git for Windows 2.55.0.windows.5.

| Check | Result |
| --- | --- |
| Muse origin test | 5 of 5 repeat runs passed after the fixture change. |
| Codex origin test | 5 of 5 repeat runs passed after the fixture change. |
| Full suite | 437 passed and 46 subtests passed in 218.31 seconds. |
| Ruff lint and watcher format check | Both passed. |
| Pyright | 0 errors, 0 warnings, and 0 informational messages for tracked source under the repo's type-check scope. |
| Report and visualizer | Both ran against a temporary seeded database. They read 6 attempts, 3 traces, 2 successes, and 3 retries. The visualizer showed $0.0120 in demo cost. |
| Credential signature scan | 0 matches in the changed text. |

The test change and this report are in the working tree. Charles has the commit, push, and CI check.
