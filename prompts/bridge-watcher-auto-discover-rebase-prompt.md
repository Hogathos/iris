# Bridge prompt: rebase auto-discover branch onto current main, resolve the test conflict

## Problem, one sentence

The auto-discover branch is based on `6789bfb` (PR #53), not current main `93383f0` (PR #54), and a by-hand rebase hits a content conflict in `tests/test_bridge_watcher.py` on commit `1ad4b71`.

## Intention / done definition

Rebase `bridge/watcher-auto-discover` onto current main, resolve the conflict, verify the tests pass on the rebased branch, push, report, stop. Done means: the branch tip sits on `93383f0`, the watcher tests and the full suite pass on the rebased branch, ruff and scoped flake8 are clean, exactly the same three implementation commits are replayed (tests, fix, report) plus a rebase report, and the report names the new base commit.

## Research, look first

Read the branch on GitHub (`bridge/watcher-auto-discover`). The tip is `6935ac0` (the report commit). The three implementation commits are `1ad4b71` (tests, 9 new auto-discover tests), `9bf5e31` (fix, discovery in `tools/bridge_watcher.py`), `6935ac0` (report). The prompt commit is `81e50d9`. The base is `6789bfb` (PR #53 merge). Current main is `93383f0` (PR #54 merge, the fetch fix).

The conflict is in `tests/test_bridge_watcher.py` when replaying `1ad4b71`. The fetch fix changed that file: it renamed `"new.md"` to `"new-prompt.md"` in the five fetch tests (12 strings). The auto-discover tests were written against the old base. Both sets must survive the merge.

## Method

1. Run `git fetch origin` FIRST, so origin/main is current. Do not rebase onto a stale ref. A rebase onto a stale ref is no rebase at all.
2. Check out `bridge/watcher-auto-discover` and run `git rebase origin/main`.
3. Resolve the conflict in `tests/test_bridge_watcher.py`. Keep BOTH the fetch-fix tests (with `"new-prompt.md"`) AND the 9 new auto-discover tests. Do not drop either set. Do not change the fix (`tools/bridge_watcher.py`) unless the rebase forces it; if it does, say so in the report.
4. Run the watcher tests, the full suite, ruff, and scoped flake8 on the rebased branch. All must pass. If a test fails, fix it on the branch and say so in the report.
5. Push with `git push --force-with-lease`. A rebase rewrites the branch; a normal push will be rejected.
6. Write the report to `prompts/bridge-watcher-auto-discover-rebase-report.md` on the branch, own commit. Name the new base commit, the conflict and how it was resolved, the test counts, and the lint results.

## Decisions

- The rebase is by hand trigger, not by the watcher. The watcher is stopped.
- The branch stays `bridge/watcher-auto-discover`. No new branch.
- Exactly the same three implementation commits are replayed. No new feature work, no spec file (skipped per the fetch-fix precedent, noted in the report).
- If the conflict cannot be resolved cleanly, stop and report the conflict, do not force it.

## Trajectory

Report the new base commit, the conflict resolution, the test counts (watcher file and full suite), the lint results, the push result, and what is still open. Then stop. No PR, no merge. Merging needs Charles's words.
