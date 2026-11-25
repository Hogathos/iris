# Watcher push-after-run report (retry, verification run)

Branch: `bridge/watcher-push`. No merge performed. No PR opened.

## Challenge the frame (once)

A simpler route exists: hand the sandboxed run a token so it pushes itself. That places a secret in sandbox logs and prompts. The watcher push is sturdier: it uses the existing credential helper and handles no secret. I built what the prompt says.

## Base

`git fetch origin` failed in the sandbox: `schannel: SEC_E_NO_CREDENTIALS`. The fetch failure proves the same block this feature fixes.

Correction to the prior report on this branch: it claimed local `origin/main` (`8bab364`, PR #59) was older than the base (`614d8c2`, PR #60) and skipped the rebase. The ancestry check reverses that: `614d8c2` is an ancestor of `8bab364`. Main had moved. PR #59 merged after PR #60.

Freshness without fetch: one GitHub API call (`commits/main`) returns `8bab364`, the same SHA as the local ref. Two routes agree. `8bab364` is the current main.

Action: rebased the branch onto `origin/main` (`8bab364`). Four commits replayed clean, no conflicts, no merge. New base commit: `8bab364`.

## Tests commit

`01db743` (rebased from `46bcde5`): "Bridge watcher push-after-run: failing tests (12 new)". It touches only `tests/test_bridge_watcher.py` (362 insertions).

Fail-first was reproduced this run in a scratch worktree at the pre-implementation tree. `PushBranchCommandTest` plus `PushAfterRunTest`: 12 failed, 66 deselected. Cause: `push_branch` did not exist (`AttributeError`), and `run_once` never pushed. The worktree was removed after.

## Implementation commit

`c04fd8f` (rebased from `040b108`): "Bridge watcher push-after-run: push branch after each run, log it, never force". It touches only `tools/bridge_watcher.py` (46 insertions, 2 deletions). The tests were not edited during implementation.

## Retry prompt file

Cherry-picked `4594cd1` with `-x` as `0212f21`. It holds only `prompts/bridge-watcher-push-retry-prompt.md` (the retry prompt text, 67 lines). Reason: it lived only on the remote branch, and a hand force-push of this branch would drop it. A cherry-pick is not a merge. No code from any other branch was taken. This is the one file outside the two touched files, disclosed here with its reason.

## What the push step does

After each `muse exec` run finishes, whatever its exit code, `run_once` calls `push_branch(repo_dir, branch)`: exactly `git push origin <branch>`.

The run is marked seen first and the launch is logged. The push is a separate step after that. Success logs `push branch=<b> pushed to origin`. Failure logs `push branch=<b> push failed (<error>)` in the same log as the run entries.

It sits on the shared run path, so config-listed and auto-discovered branches push alike. It is gated on `repo_dir`: with no tree to push from, the run completes with no push attempt and no crash.

## What it does not do

No force-push under any flag: the exact argv is pinned by test, and `force` occurs in the module only in prose that forbids it. No rebase: a behind branch (non-fast-forward) is logged and left alone, and the test asserts no checkout or fetch call on that path. No retry loop: one attempt per run. A failed push never unmarks the run, never re-runs it, and never crashes the poll. No push happens without a run: report-skip, dirty tree, fetch failure, missing branch, and baseline all push nothing, each pinned by test.

## No merge

No branch was merged into this one, for any reason. History operations this run: `git rebase origin/main` (4 commits, clean) and `git cherry-pick -x 4594cd1` (1 file, clean). The log is linear.

## Checks

- Watcher file: 78 passed (66 old + 12 new).
- Full suite: 226 passed, 68 failed, 74 errors. The counts match the pre-existing baseline exactly. Zero failures mention `bridge_watcher`, and only `tests/test_bridge_watcher.py` imports it. Sample cause: `PermissionError: [WinError 10013]` — the sandbox forbids socket binding, and those tests (proxy, bench, networking) start local servers. Environmental, pre-existing, unrelated to this diff.
- `ruff check .`: clean.
- `flake8 --select=E9,F63,F7,F82 .`: findings only in `.venv` third-party files. The touched files are clean (exit 0).
- Falsification: the new tests attack the implementation instead of confirming it. Four tests assert pushes that must not happen (report skip, dirty tree, fetch failure, baseline). Two assert a failure that must not crash the poll (logged, seen stays, next poll quiet, no rebase calls). One pins the exact argv with no force spelling. Extra review pass: `force` and `rebase` appear only in forbidding prose. Residual risk, not new: if `_log_push` itself raised (disk full), the poll would crash. That is the same exposure class as the existing `log_launch`. It stays as is.
- `git status` shows a clean tree. No untracked files. The scratch worktree was removed.
- No spec file for this change, per the watcher-fix precedent (report-skip, fetch-fix, auto-discover, token-auth all skipped it).

## Push result

`git push origin bridge/watcher-push` failed with `schannel: SEC_E_NO_CREDENTIALS` (exit 128), the same block this feature fixes. Commits stay local. The branch has diverged from the remote (local holds the rebased work plus the cherry-picked prompt; remote holds the prompt on the old base), so the hand push needs a force-push or Charles's chosen reconcile. Nothing is lost either way: the remote prompt content now also lives locally as `0212f21`.

## Unknowns

- Whether the full suite is green on the Dell with network. The socket failures need a run outside the sandbox.
- The exact hand-push sequence Charles will use to reconcile the diverged branch.

This feature should make that manual push the last one.
