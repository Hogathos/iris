# Bridge prompt: watcher auto-discovers prompt branches, no config edit, no cascade

## Problem
Every new prompt branch needs a hand step on the Dell: edit `bridge_watcher_config.json` to add the branch to the `branches` list, then restart the watcher. That breaks the flow Charles wants: start the watcher and walk away while it runs prompts and Main monitors each run. The watcher already lists files through the GitHub API. It should list branches the same way and pick up any branch with a new prompt, with no config edit and no restart.

The danger is a cascade. Auto-discovery must never re-run an old prompt on an old branch. Three failures Nov 24 prove the risk: the teardown prompt ran again by hand, a report file ran as a prompt, and old watcher prompts ran because they were never marked seen. This fix must be extra careful. A stale prompt running again is worse than a new prompt waiting.

## Intention / done definition
The watcher discovers prompt branches on its own. Charles starts the watcher and walks away. Any branch on GitHub with a new, genuine, unreported prompt file gets run exactly once. No branch is ever added to a config file by hand. No old prompt ever runs again, on any branch, for any reason. The watcher never runs a prompt twice and never loses uncommitted changes.

## The fix
In `tools/bridge_watcher.py`:

- Add branch discovery through the GitHub API. The watcher already has a `lister` that lists files in `prompts/` on a branch. Add a branch lister that lists all branches in the repo through the GitHub API (the same API the file lister uses, same auth, same timeout). Discovery runs in addition to the config `branches` list, not instead of it: the watched set is the union of the config list and every branch the API returns. A branch in the config is always watched. A branch found by discovery is watched too. Nothing needs to be removed from the config, and nothing needs to be added to it.
- Discovery happens once per poll, before the branch loop. A branch-listing failure skips discovery for that poll (log it plainly, retry next poll) and the watcher still processes the config list. Discovery never crashes the poll.
- The anti-cascade rules, all of them, apply to discovered branches exactly as they apply to config branches. No exceptions:
  - Only files whose name ends in `-prompt.md` are runnable. Any other file is never downloaded, never run, never logged as a failure. This is the report-skip fix's filter, applied here too.
  - A prompt whose report file (`<name>-report.md`) exists in the branch listing is skipped: marked seen, logged in plain words, never downloaded or run. This is the report-skip fix's rule, applied here too.
  - A prompt in the seen-list is never run again, on any branch. The seen-list is global and is the source of truth. A filename seen once is done everywhere.
- Baseline on first discovery. The very first poll after this fix lands must baseline, not run. It lists every branch, lists every prompt file on every branch, and marks them all seen without running any of them, exactly like the existing `_baseline_branch` does for config branches, but across every discovered branch. This is what stops the cascade: every old prompt on every old branch is marked seen before any new prompt can run. A later poll runs only files that appeared after that baseline. If the baseline cannot list every branch cleanly, the next poll stays in baseline mode instead of running.
- Do not change the dirty-tree check, the checkout logic, the fetch behavior, or the run command. Discovery only changes which branches are considered. The checkout, fetch, and run steps are unchanged.
- Keep the `branches` config key working as before. Do not remove it, do not require it. Discovery is additive.

## Tests first
Write the failing tests first, as their own commit, then the fix as its own commit. Add the tests to `tests/test_bridge_watcher.py`, in the existing `RunOnceTest` style (fake lister, downloader, runner; temp dir for state, log, downloads; fake the branch lister the same way the file lister is faked):

- A prompt on a branch found only by discovery (not in the config list) is downloaded and run.
- A prompt on a discovered branch that is already in the seen-list is never downloaded or run.
- A prompt on a discovered branch whose report exists in the listing is skipped: marked seen, logged, never downloaded or run.
- A non-prompt file (`*-report.md`, any other name) on a discovered branch is never downloaded or run.
- The first poll after the fix baselines every discovered branch: all prompt files marked seen, nothing downloaded, nothing run, even on branches never watched before.
- A branch-listing failure skips discovery for that poll, logs it plainly, and the config branches still process normally.
- A prompt that appears on a discovered branch after the baseline runs exactly once, and a second poll runs nothing for it.
- A prompt on a config branch still runs as before (no regression).

## Verification
- The new tests fail on the tests-only commit and pass on the fix commit.
- The full suite stays green.
- Ruff and flake8 clean (the repo's selection: E9, F63, F7, F82).
- Exactly two files changed: `tools/bridge_watcher.py` and `tests/test_bridge_watcher.py`. No other files.

## Report
Write the report to `prompts/bridge-watcher-auto-discover-report.md` on this branch and push it as its own commit, separate from the test and fix commits. The report states the commits, the test results (fail-before, pass-after, full suite count), what changed, and what stays open. Name the anti-cascade guarantees explicitly: baseline on first discovery, seen-list as global source of truth, prompt-only filter, report skip. Do not open a PR. Do not merge. Branch only.

## Frame check
The simpler alternative is keeping the manual config edit: Main gives Charles the exact `branches` line each time and he pastes it. That was the standing rule and it works, but it is a hand step every time and it breaks the start-and-walk-away flow Charles asked for. The durable fix is this one, with the baseline as the anti-cascade guarantee. No further frame challenge is needed; do the fix above.
