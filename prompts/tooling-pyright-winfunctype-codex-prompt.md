# Prompt: Fix Pyright WINFUNCTYPE error blocking PR #68 CI

## Challenge the frame
Before starting: if a simpler or sturdier fix exists than the one described here, say so once, up front, then do what this prompt asks. The obvious simpler fix is a one-line `# pyright: ignore[reportAttributeAccessIssue]` on the `@ctypes.WINFUNCTYPE(` line. If that is the right fix, do it. If the Windows-only code needs a real guard instead (platform check, restructure), do that. Choose the fix that is correct, not the one that is shortest.

## Problem
PR #68 (Tooling: drop flake8, Ruff enforces lint plus format) CI fails on macOS and Ubuntu because Pyright reports `ctypes.WINFUNCTYPE` does not exist on non-Windows platforms, at `smoke_packaging.py:94:13`. Windows CI passes. The reformat itself is clean (Ruff lint and format both pass in CI); this Pyright error is what blocks the PR.

## Intention / done definition
Done when:
1. `pyright` passes on `smoke_packaging.py` on a non-Windows platform (the Pyright error at line 94 is resolved).
2. `ruff check .` passes.
3. `ruff format --check .` passes (the file stays formatted).
4. The full test suite passes (`python -m pytest tests/ -q`), same count as the PR #68 baseline (455 passed, 51 subtests).
5. The fix does not change runtime behavior on Windows (the smoke test still works there; the `WINFUNCTYPE` code still runs on Windows).
6. No new Pyright errors are introduced.

## Research
Look first, then decide:
- Read `smoke_packaging.py` around line 94 (the `@ctypes.WINFUNCTYPE` decorator in `_window_visible`). Note the existing `# pyright: ignore[reportAttributeAccessIssue]` comments on the `ctypes.windll.user32` line and on a `ctypes.c_void_p` line in the same function. The flagged line is the decorator itself:
  ```python
  @ctypes.WINFUNCTYPE(
      ctypes.c_bool,
      ctypes.c_void_p,  # pyright: ignore[reportAttributeAccessIssue]
      ctypes.c_void_p,
  )
  ```
- Check git history for `smoke_packaging.py` on this branch vs main: did the Ruff format reformat (commit `855c35c`, 80 files) move the decorator or the ignore comments? PR #67 CI passed on the same platforms, so find what changed in this function and why Pyright now flags line 94.
- Check `pyrightconfig.json` for the Pyright configuration (what files are checked, what rules are enabled).
- Check the CI workflow `.github/workflows/python-app.yml` for how Pyright is invoked (which step, which files, which Pyright version).

## Method
- This is a small, single-file fix. No plan gate needed (touches 1 file, 1 function).
- Determine the root cause first (why Pyright flags line 94 now, when PR #67 passed). The fix should address the cause, not just silence the error — but if a `# pyright: ignore` on the right line is the correct fix (matching the existing ignores in the same function), that is acceptable. Do not add a broad ignore or disable Pyright for the file.
- If the fix is a platform guard (e.g., only defining/using WINFUNCTYPE on Windows), make sure the function still returns False on non-Windows (it already does via the `try/except AttributeError` around `ctypes.windll.user32` — confirm the guard preserves that behavior).
- After the fix, run: `ruff check .`, `ruff format --check .`, `pyright` (on the repo, or at least on `smoke_packaging.py`), and the full test suite. Report the exact results. Use `activate.bat` or call `.\.venv314\Scripts\python.exe` directly for Python commands.
- This is a type-check fix, not new behavior, so no failing-test-first is required. Verify by running Pyright and the suite, and report the exact results.
- Make the edits in the working tree. Do NOT run `git commit` or `git push`. Do NOT merge. Write your report to `prompts/tooling-pyright-winfunctype-codex-report.md` on this branch.

## Verify
- Verify by read-back: after the fix, run Pyright and confirm the `WINFUNCTYPE` error is gone. Run the full suite and confirm 455 passed, 51 subtests (same as baseline). If Pyright still errors or the suite count changes, the fix is not done — report what failed and why.
- Verdict: Correct (Pyright clean, suite green, Windows behavior unchanged), Incorrect (Pyright still errors, or suite regresses, or Windows behavior changes), or Unknown (could not verify, e.g., Pyright not available in this environment — report what could not be checked).

## Report
Write `prompts/tooling-pyright-winfunctype-codex-report.md` with:
- What the root cause was (why Pyright flags line 94 now).
- What you changed (file, line, exact change).
- Verification results: `ruff check`, `ruff format --check`, `pyright`, full suite (with counts).
- What is still Unknown (if anything).
- Your session usage (Input, Cached, Output, Total, Turns) from the usage panel, if available.
