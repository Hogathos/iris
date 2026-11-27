# Hooks: fix cross-platform path canonicalization (PR #66 CI failures)

## Problem

PR #66 (branch `tooling/hooks-security-fixes`) has 2 failing CI checks: `build-quick (macos-latest, 3.12)` and `build-quick (windows-latest, 3.12)`. Both fail in the "Test with pytest" step with the SAME two tests failing. Ubuntu build-quick (3.10, 3.12, 3.14) passes. All earlier steps pass on both failing runners (checkout, Set up Python 3.12, Install dependencies, Lint with Ruff, Lint with flake8, Type-check with Pyright). CI run 45954936875.

## Failing tests

1. `tests/test_hooks.py::TestHookInstaller::test_installs_in_common_hooks_path_from_linked_worktree` (line 467)
2. `tests/test_muse_hooks.py::TestUnplannedFileWrites::test_subdirectory_uses_root_plan` (line 438)

On both runners pytest reports 2 failed, 450 passed, 51 subtests passed.

## Root cause (from CI log read-back)

Paths are compared without canonicalizing them first, so the same directory in two spellings does not compare equal:

- macOS: `/var` is a symlink to `/private/var`. The installer returns the resolved `/private/var/...` path; the test expects the unresolved `/var/...` path.
- Windows: the 8.3 short-name form `RUNNER~1` vs the long form `runneradmin` for the same user profile directory.
- The second test fails the same way: the hook builds a relative path full of `../` segments because the plan root and the resolved file path are in different spellings, so it does not match the plan entry `docs/notes.md` and denies a write it should allow (exit code 2).

This is a real bug in the new code, not a CI flake and not a pre-existing failure. The hook logic is fine on the main (Ubuntu) path, but the path comparison is not robust on macOS and Windows when a path goes through a symlink or a short name.

## Fix

Canonicalize/resolve both sides of every path comparison in the hook installer and in the plan-protection path matching before comparing — e.g. `Path.resolve()` / `os.path.realpath`, and on Windows the long-path form — so the same directory always spells the same way. Apply the fix in the hook code, not only in the tests, so a real user whose path goes through a symlink or short name gets the correct behavior, not just CI.

## Challenge the frame

Before implementing, if a simpler or sturdier way exists than canonicalizing at each comparison site, say so once, up front, in the report. Then do the chosen approach. Challenge the frame, not just the details.

## Method (test-first)

1. The two tests above ARE the failing tests. Confirm they reproduce the failure, then implement the fix. Do not stop for approval at any point in this run — this session is non-interactive, so work through to the report.
2. Implement the canonicalization fix in the hook code (`hooks/` and `.muse/hooks/` as needed).
3. Run the full test suite (`python -m pytest tests/ -q`) and confirm the two failing tests now pass and nothing else regressed. Run Ruff check, Ruff format on changed Python files, and Pyright. Report the exact counts.
4. Keep the change scoped to the path-canonicalization fix. Do not reformat the whole repo and do not touch unrelated files.

## Report

Write the report to `prompts/tooling-hooks-path-canonicalization-codex-report.md` on this branch. Include: what changed (files), the fix approach, full test results (before/after), lint/type results, and anything still open.

Note: you cannot commit or push (Git is read-only in this workspace); Charles does the git part. Do not claim a push or CI result you did not produce.
