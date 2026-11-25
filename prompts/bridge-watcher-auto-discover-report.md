# Bridge watcher auto-discover: report

## Summary

The watcher discovers prompt branches on its own now. Charles starts the watcher and walks away. Any branch on GitHub with a new, genuine, unreported prompt file runs exactly once. No branch needs a hand config edit.

## Commits

- Prompt: `81e50d9` — the order file (rebased onto `6789bfb`, current main, with Charles's approval, so discovery inherits the report-skip filter).
- Tests: `1ad4b71` — Add failing auto-discover watcher tests. It adds `DiscoveryTest` (8 tests) and `WatchedBranchesPropertyTest` (1 Hypothesis property test) to `tests/test_bridge_watcher.py`. No source change.
- Fix: `9bf5e31` — Discover prompt branches through the GitHub API in bridge watcher. It changes only `tools/bridge_watcher.py`.
- This report is its own commit on the same branch. No PR. No merge.

## Test results

- Fail-before: on the tests-only commit, the 9 new tests fail. Command: `pytest tests/test_bridge_watcher.py -q -k "Discovery or WatchedBranches"`. Result: `9 failed, 46 deselected`. Eight fail with `TypeError: run_once() got an unexpected keyword argument 'branch_lister'`; the property test fails with `AttributeError: module 'tools.bridge_watcher' has no attribute 'watched_branches'`.
- Pass-after: on the fix commit, the full watcher file passes. Command: `pytest tests/test_bridge_watcher.py -q`. Result: `55 passed`.
- Full suite: `pytest tests/ -q`. Result: `345 passed, 40 subtests passed`.
- Lints: `ruff check` on both changed files passes. `flake8 --select=E9,F63,F7,F82` on both changed files is clean.
- CI for the exact commits was not checked from this machine. Treat CI as Unknown.

## What changed

- Added `github_list_branches(repo)`: every branch name through the GitHub branches API, same headers and timeout as the file lister, with page following so a repo with more branches than one page holds still returns them all. Network errors rise to the caller.
- Added `watched_branches(configured, discovered)`: the watched set is the config list first, then each discovered branch not already listed, each once.
- `run_once` takes an optional `branch_lister` (no lister means the old path exactly: config branches only). Discovery runs once per poll before the branch loop. A branch-listing failure is logged plainly, skips discovery for that poll, and the config branches still process.
- `main` passes a live branch lister (`github_list_branches`), so the running watcher discovers.
- Only `tools/bridge_watcher.py` and `tests/test_bridge_watcher.py` changed. The dirty-tree check, checkout logic, fetch behavior, and run command are unchanged.

## Anti-cascade guarantees, named

- Baseline on first discovery: the first poll after discovery lands marks every prompt on every watched branch seen and runs nothing, through a marker file next to the seen-list. A poll that cannot list every branch cleanly writes no marker, so the next poll stays in baseline mode.
- Seen-list as global source of truth: a filename seen once never runs again, on any branch.
- Prompt-only filter: files not ending in `-prompt.md` are never downloaded, never run, never logged as failures, on discovered branches exactly as on config branches.
- Report skip: a prompt whose `<name>-report.md` sits in the same branch listing is marked seen and skipped in plain words, never downloaded or run, on discovered branches exactly as on config branches.

## What stays open

- The live GitHub branch listing never ran here: the tests fake the branch lister, and this machine made no API call. Pagination and the marker path work in tests but not against the real API yet.
- The per-feature spec file was skipped: the order allows exactly two changed files, and Charles approved skipping it. This report carries the decision.
- No PR. No merge. Branch only, as ordered.
