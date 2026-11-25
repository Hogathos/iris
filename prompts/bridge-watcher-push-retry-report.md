# Watcher push-after-run report (retry)

Branch: `bridge/watcher-push`. No merge performed. No PR opened.

## Challenge the frame (once)

A simpler route exists: hand the sandboxed run a token so it pushes itself. That moves a secret into sandbox logs and prompts. The watcher push is sturdier: it uses the existing credential helper and handles no secret. I built what the prompt says.

## Base

`git fetch origin` failed in the sandbox: `schannel: SEC_E_NO_CREDENTIALS`. The live main on GitHub could not be checked.

At start this branch held exactly one commit on main-line: `70805d9` (prompt) on `614d8c2` (merge of PR #60, token-auth). That matches the retry prompt's expected pre-work state.

No rebase was done. The local `origin/main` ref is `8bab364` (PR #59): stale and older than the base. Rebasing onto it would move backwards. `614d8c2` is the newest main-line commit known locally. History was left alone.

## Tests commit

`46bcde5` — "Bridge watcher push-after-run: failing tests (12 new)".

Targeted run before implementation: 12 failed, 66 deselected. Each failed as expected: `push_branch` did not exist (`AttributeError`), and `run_once` never pushed. Only `tests/test_bridge_watcher.py` was staged.

## Implementation commit

`040b108` — "Bridge watcher push-after-run: push branch after each run, log it, never force". Only `tools/bridge_watcher.py` was staged (46 insertions, 2 deletions).

## What the push step does

After each `muse exec` run finishes, whatever its exit code, `run_once` calls `push_branch(repo_dir, branch)`: exactly `git push origin <branch>`.

The run is marked seen first. The push is a separate step after that. Success logs `push branch=<b> pushed to origin`. Failure logs `push branch=<b> push failed (<error>)` in the same log as the run entries.

It runs on the shared run path, so config-listed and auto-discovered branches push alike. It is gated on `repo_dir`: with no tree to push from, the run completes with no push attempt.

## What it does not do

No force-push under any flag: the exact argv is pinned by test, and `force` appears in the module only in prose that forbids it. No rebase: a behind branch (non-fast-forward) is logged and left alone. No retry loop: one attempt per run. A failed push never unmarks the run, never re-runs it, never crashes the poll. No push happens without a run: report-skip, dirty tree, fetch failure, and baseline all push nothing (each pinned by test).

## No merge

No branch was merged into this one, for any reason. The base had not verifiably moved (fetch blocked, local ref stale), so no rebase stood in for it either.

Mid-session incident: this same repo checkout was switched to `app-mode/implementation` and rebased there by outside hands while I worked. My uncommitted implementation diff rode along untouched (git carries it). I switched back to `bridge/watcher-push` with `git checkout`, no merge, and verified the diff was byte-identical to what I wrote. The app-mode tree was left without my change.

## Checks

- Watcher file: 78 passed (66 old + 12 new).
- Full suite: 226 passed, 68 failed, 74 errors. Every failure sits outside the watcher (bench, appmode, devtool files). None of those files imports `bridge_watcher`. Sample cause: `PermissionError: [WinError 10013]` — the sandbox forbids binding sockets, and those tests start local servers. Environmental, pre-existing, unrelated to this diff.
- `ruff check .`: clean.
- `flake8 --select=E9,F63,F7,F82` on the two touched files: clean. A repo-wide run flags only `.venv` third-party files.
- Falsification: the new tests attack the implementation, not confirm it. Four tests assert pushes that must not happen (report skip, dirty tree, fetch failure, baseline). Two assert a failure that must not crash the poll (logged, seen stays, next poll quiet, no rebase calls). One pins the exact argv with no force spelling.
- `git status` shows only the intended files. No untracked files.

## Files

No unplanned files. The retry prompt file (`prompts/bridge-watcher-push-retry-prompt.md`) exists only on `origin/bridge/watcher-push` as `4594cd1`; I read it through `git show` and did not materialize it locally, per the only-touch-two-files rule. No spec file, per the watcher-fix precedent. `tools/bridge_watcher_README.md` was left untouched for the same reason; it does not document the push step yet.

## Divergence note

Local and remote have diverged, 1 and 1 at last check. Remote holds `4594cd1` (the retry prompt). Local holds the tests commit, the implementation commit, and this report. The hand push must reconcile both sides. My commits were never rewritten after pushing became impossible.

## Unknowns

- The true current `origin/main` on GitHub (fetch blocked).
- Whether the full suite is green on the Dell with network (the socket failures need a run outside the sandbox).
- The state of the `app-mode/implementation` tree after the mid-session switch (not re-verified; left alone by design).
- The exact hand-push sequence Charles will use to reconcile the diverged branch.

My push from this run will be blocked (`SEC_E_NO_CREDENTIALS`), the same block this feature fixes. Commits are local. This feature should make that manual push the last one.
