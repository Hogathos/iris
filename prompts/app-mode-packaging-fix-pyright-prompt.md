# App mode packaging — fix pyright on smoke_packaging.py (CI red)

Fix the CI failure on PR #43 (branch app-mode/packaging, commit 9bff323).

## The failure

CI run 45362644202: Windows 5/5 green. Ubuntu + macOS 0/10 red.
The failing step is pyright, on `smoke_packaging.py` only.

Pyright errors, reproduced locally on Linux:
- `smoke_packaging.py:88:25` — error: `"windll" is not a known attribute of module "ctypes"` (reportAttributeAccessIssue)
- `smoke_packaging.py:93:13` — error: `"WINFUNCTYPE" is not a known attribute of module "ctypes"` (reportAttributeAccessIssue)

Cause: `ctypes.windll` and `ctypes.WINFUNCTYPE` exist only on Windows.
On Linux and macOS pyright cannot resolve them, so it reports errors.
On Windows pyright resolves them, so it passes. That is the whole
pattern: Windows green, Ubuntu + macOS red.

## Do

Add a pyright ignore comment to the two lines, so pyright passes on
every platform and the Windows-only code is unchanged in behavior:
- Line 88 (`user32 = ctypes.windll.user32`): append
  `# pyright: ignore[reportAttributeAccessIssue]`
- Line 93 (`@ctypes.WINFUNCTYPE(...)`): append
  `# pyright: ignore[reportAttributeAccessIssue]`

Do not change the runtime behavior. Do not change any other file.
Do not change the smoke test logic. This is a lint-only fix.

## Verify

- Run `python -m pyright smoke_packaging.py`. It must report 0 errors.
- Run the full suite (`python -m pytest tests/ -q`) plus
  `ruff check .` and `flake8 --select=E9,F63,F7,F82 .`. All must pass.
- Run `python -m pyright` (whole repo) if the workflow does. It must
  report 0 errors.

## Rules

- Commit named files only (`smoke_packaging.py`). Never `git add -A`,
  never `git commit -a`. Never commit `.exe`, `dist/`, `build/`,
  `*.db`, `.pytest_cache/`, or secrets.
- Push the branch only. Do NOT open a PR or merge.
- Report the commit hash, the pyright result, the suite result, and
  your session usage (Input, Cached, Output, Total, Turns, Current %,
  Weekly %) from the usage panel. Then stop.
