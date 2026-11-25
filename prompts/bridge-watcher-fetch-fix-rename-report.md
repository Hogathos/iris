# Bridge watcher fetch fix: rename report

## Summary

The five fetch tests use a prompt filename now. The watcher file passes. The fetch fix does not change.

## Commit

- Rename: `fd2980b` — Rename fetch-test prompt filenames to prompt files. It changes only `tests/test_bridge_watcher.py`. It sits on top of the rename prompt `58e0fac`. Normal push, no force-push. No PR. No merge.

## Test results

- Watcher file: `.venv/Scripts/python.exe -m pytest tests/test_bridge_watcher.py -q`. Result: `51 passed`.
- Full suite: `.venv/Scripts/python.exe -m pytest tests/ -q`. Result: `341 passed, 40 subtests passed`.
- Ruff: `.venv/Scripts/python.exe -m ruff check .`. Result: `All checks passed`.
- Flake8 scoped: `.venv/Scripts/python.exe -m flake8 --select=E9,F63,F7,F82 tools/bridge_watcher.py tests/test_bridge_watcher.py`. Result: clean, no findings.
- Flake8 full: `flake8 --select=E9,F63,F7,F82 .` reports only third-party files under `.venv/` and `.venv314/`. No finding in `tools/bridge_watcher.py` or `tests/test_bridge_watcher.py`.

## What changed

- Renamed `"new.md"` to `"new-prompt.md"` in 12 places in `FetchMissingBranchTest` in `tests/test_bridge_watcher.py`. Five tests: fetchable remote, fetch failure, still missing after fetch, existing local branch, second poll.
- The lister, the expected launched entries, and the seen-list assertions use the new name. No test logic changes. No source changes.

## What stays open

- CI for the exact commit was not checked from this machine. Treat CI as Unknown.
- No PR. No merge. Branch only, as ordered.
