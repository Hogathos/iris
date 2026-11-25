# Tooling: drop flake8, Ruff for lint + format

## Base and frame challenge

Fetch origin FIRST. Rebase onto `origin/main` if it has moved since your last fetch, and report the base commit you built on. Do not assume the base. Verify it and report it.

Challenge the frame before you start: if a simpler or sturdier way to drop flake8 and move to Ruff for both linting and formatting exists than the steps below, say so once, up front, in your report, then do the steps as written. Do not nag the point. Silence means you checked and this is the best path.

## Decision (already made)

Drop flake8 from the checks. Use Ruff for both linting (`ruff check`) and formatting (`ruff format`). This was verified against the repo: Ruff's config already selects exactly the set the CI flake8 run enforced (`E9, F63, F7, F82`), and the config comment says "Ruff replaces flake8." Flake8 adds nothing to the gate. The two informational flake8 passes (complexity, line length) run with `--exit-zero`, so they gate nothing.

## Steps, in order

### 1. Remove flake8 from CI

In `.github/workflows/python-app.yml` (all jobs that have it):
- Remove the "Lint with flake8" steps entirely (both the hard-error gate and the informational pass).
- Remove `flake8` from the `pip install` lines (keep `ruff pyright pytest hypothesis fastapi uvicorn` and whatever else is there).
- Do not change the Ruff steps' gate. The hard-error gate stays `ruff check .` with the config selection (`E9, F63, F7, F82`).

### 2. Normalize formatting once

Run `ruff format` on the repo. Commit the result as its own commit, separate from the CI change, with a clear message (e.g. "Format with ruff format"). This is a one-time normalization. Do not hand-edit files to change formatting; let the formatter do it. Do not change code logic, names, or comments while formatting. If `ruff format` refuses a file or produces something that looks wrong, say so in the report and leave that file out, do not force it.

### 3. Add the format check to CI

After the normalize commit, add a `ruff format --check` step to the CI workflow (in each job that lints), so formatting is enforced going forward. It must pass on the normalized tree. If it does not pass, the normalize step is incomplete; fix that before finishing, do not add a failing check.

### 4. Rule selection — latitude on extras

The hard-error gate stays `E9, F63, F7, F82`. You may add additional Ruff rules that are clearly beneficial AND pass cleanly on the codebase (no new failures, no mass fixes needed). If you find extras worth adding, add them to the Ruff config, name them in the report, and say why. If a rule you consider would create a pile of new failures, do not add it; name it in the report as a candidate for later instead. Do not weaken the existing gate. Do not add a rule just to have more rules.

## What not to do

- Do not change any test. Do not change product code logic. This is a tooling change only.
- Do not change `pyrightconfig.json` or the CodeQL workflow.
- Do not push. The sandbox blocks the push. Commit locally and report. The branch is pushed by hand.
- Do not merge. Do not open a PR. This is a change on its own branch.
- Do not reformat by hand. Only `ruff format` changes formatting.

## Verification

- `ruff check .` clean.
- `ruff format --check` clean (after the normalize commit).
- Full test suite green on the Dell (the socket tests fail there at bind, WinError 10013 — that is environmental, not a regression; report the counts and confirm the failure set is identical with the change stashed).
- `pyright` — run it if it works on the Dell; if the sandbox breaks it, say so and leave it Unknown for the VM re-check. Do not guess a Pyright result.
- Confirm the CI workflow still parses (the workflow file must be valid YAML; a broken workflow is a failure of this change).

## Report

Write your report to `prompts/tooling-drop-flake8-ruff-format-report.md`, own commit. It must state: the base commit you built on; the frame challenge (what you checked, one line); what was removed from CI (flake8 steps and install lines); the normalize commit (how many files changed); the format-check step added; any extra Ruff rules added and why, plus any candidates you left out and why; the test-suite counts with and without the change (stashed baseline); ruff check and ruff format --check results; and what is still unknown.
