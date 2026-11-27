# Tooling report: drop flake8, Ruff for lint plus format

Branch: `tooling/drop-flake8-ruff-format`. Four commits plus the prompt and
this report. Nothing pushed. No merge. No PR.

## Base

Base is `f7be149` (Merge pull request #67, watcher run-status race). The
branch was originally based on `8bab364`; this run fetched successfully and
found `origin/main` had moved to `f7be149`, so the branch was rebased onto it.

The remote task branch still points at the prompt commit only. A hand push
will therefore need `--force-with-lease`.

## Frame challenge

I checked a one-commit squash and a no-normalize option. The staged order
wins: each commit stays green and blame stays clean. `ruff.toml` already held
the exact gate set, and no stray flake8 config file exists in the repo. The
steps as written are the best path.

## CI removal (commit `122ac7d`)

File: `.github/workflows/python-app.yml`. Change in all three jobs
(`build-minimal`, `build-quick`, `build-full`):

- Removed the three `Lint with flake8` steps (hard gate plus the
  `--exit-zero` informational pass).
- Removed `flake8` from the three `pip install` lines. They now read
  `pip install ruff pyright pytest hypothesis fastapi uvicorn`.
- Kept the Ruff steps byte for byte. The hard gate stays `ruff check .`
  with the config selection (`E9, F63, F7, F82`). The informational
  `ruff check . --select=E,F --exit-zero` pass is untouched.

No `flake8` string remains in the workflow.

## Normalize (commit `855c35c`)

Ran `ruff format` (Ruff 0.17.6) over the repo. The commit changes 80 tracked
files. On the rebased tree, Ruff reformatted 13 files that `origin/main` had
changed or added since the first pass; the rest were carried from the
original normalize pass. No hand edits. No logic, name, or comment changes.

One file was left out on purpose: `competitor-verification-fixtures.md`.
Ruff also scans Markdown code fences, and a dry run on that historical
fixture rewrote its fenced Python as if it were product source. That looked
wrong, so the file is excluded by name in `[format]`. All other Markdown is
still in the format gate.

## Format check (commit `6858c58`)

Added one step to each linting job, after `Lint with Ruff`:

- `Check formatting with Ruff`, which runs `ruff format --check .`.

The same commit adds a `[format]` section to `ruff.toml` excluding only
`competitor-verification-fixtures.md`. Python files and every other Markdown
file stay fully enforced.

## Extra rules

None added. The gate stays `E9, F63, F7, F82`. Probes on the final tree:

- Full `E`: 15 findings (12 E402, 2 E501, 1 E731). Left out.
- Full `F`: 30 findings (19 F401, 7 F841, 3 F541, 1 F811). Left out.
- `I` (isort): 25 findings (I001). Left out.
- `UP` (pyupgrade): 71 findings (43 UP041, 25 UP017, 3 UP015). Left out.
- `B` (bugbear): 11 findings (6 B904, 3 B905, 2 B007). Left out.
- `W`: clean, but the formatter already owns trailing whitespace, so it
  adds nothing. Not added.

Each rejected set needs its own cleanup pass first. Best candidates for
later: F401/F841/F811/F541 (dead imports and variables), then I001 (import
order), then B904/B905 (raise-from and zip strictness), then UP041/UP017
(timeout and datetime aliases). E501 conflicts with the 127-column comment
style, so it needs a decision before any adoption.

## Verification

- `ruff check .`: `All checks passed!`
- `ruff format --check .`: `217 files already formatted`.
- Workflow YAML: parses with PyYAML. The format-check step is present in
  all three lint jobs. No flake8 reference remains.
- Full suite (`python -m pytest tests/ -q`, `.venv314`, `PYTHONPATH=.`):
  with change: 455 passed, 51 subtests passed, 0 failed, 0 errors.
  Baseline at `f7be149` in a clean worktree: 455 passed, 51 subtests passed,
  0 failed, 0 errors. The WinError 10013 bind failures from the earlier
  sandbox report did not reproduce here.
- `pyright` on tracked Python files: 16 errors, 1 warning. Every error is an
  unresolved `PySide6` or `shiboken6` import; the warning is unresolved
  `yaml`. The local venv does not have those packages. The baseline at
  `f7be149` has the identical 16 errors and 1 warning. A clean Pyright result
  remains Unknown until CI or the VM installs `requirements.txt`.
- `pyrightconfig.json` and the CodeQL workflow: untouched.
- Tests changed only by `ruff format`; no test logic changed. Product code
  logic, names, and comments are unchanged.
- Tree is clean except the commits plus this report.

## Still unknown

- Pyright result with `requirements.txt` installed (local result is
  environment-limited; see above).
- CI green for the exact commits (needs a push plus Actions run).
- Whether `origin/main` moves again before the hand push.
