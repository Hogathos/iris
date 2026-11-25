# Watcher push-after-run prompt

Make the watcher push the branch after each run it starts. This fixes the push block: Muse Code runs inside a sandbox that cannot reach the Windows Credential Manager, so its own `git push` fails with `schannel: SEC_E_NO_CREDENTIALS` and every run needs a push by hand. The watcher runs outside the sandbox, as the user, so it can use the credential helper. After a run finishes, the watcher pushes the branch itself.

## Read first

- The watcher: `tools/bridge_watcher.py`.
- Its tests: `tests/test_bridge_watcher.py`.
- The repo rules: `AGENTS.md` (watcher conventions, Co-Work safeguards).

## Challenge the frame, once

Before you write code, check once: if a simpler or sturdier way to do this exists, say so in your report, then do what this prompt says. Do not nag it again.

## Git first

Run `git fetch origin` FIRST. Then rebase this branch onto `origin/main`. Verify the base commit matches the current main on GitHub, and report the new base commit in your report. Do not build on a stale base.

## Test first

Write the failing tests first, run them, show they fail. Commit them on their own commit. Then implement. New behavior only; refactors skip the test-first step.

## Implement

After a `muse exec` run finishes (whatever its exit code), the watcher pushes the prompt's branch to origin. The rules:

- Push the branch the run was on, explicitly: `git push origin <branch>`. Never `git push --force`. Never force-push under any flag.
- Push only after an actual run completed. No push on a skip (uncommitted changes, missing branch, fetch failure, report already exists). No push on a baseline (no run happened).
- The run is marked seen as today, whatever its exit code. The push is a separate step after that. A push failure does not unmark the run and does not cause a re-run.
- Log the push result plainly, in the same log as the run entries. On success, log the branch and that the push went through. On failure, log the branch, the error, and that the push failed. A failed push never crashes the poll and never retries in a loop. The watcher keeps polling.
- If the push fails because the branch is behind (non-fast-forward), log it and move on. Do not rebase. A rebase is a separate prompt.
- This applies to config-listed branches and auto-discovered branches alike.
- No spec file for this change, per the watcher-fix precedent (report-skip, fetch-fix, auto-discover, token-auth all skipped the spec). Note that in your report.

## Checks

- Full test suite green, plus the new tests.
- `ruff check .` clean. `flake8 --select=E9,F63,F7,F82 .` clean.
- No unplanned files. If you create a file that was not in this prompt, say so in your report and why.
- Falsification: in your review of your own work, ask for a test that proves the implementation wrong (a push that should not happen, a failure that should not crash the poll), not one that confirms it is right.

## Git safeguards

Stage named files only. Never `git add -A`. Never `git commit -a`. Never commit `.exe`, `dist/`, `build/`, `*.db`, `.pytest_cache/`, `*.spec`, or secrets. Report the commit hash, the push result, and the test results, then stop.

Note: your own push from this run will be blocked by the sandbox (`SEC_E_NO_CREDENTIALS`), the same block this feature fixes. Commit locally and report. The branch will be pushed by hand. This feature is what makes that manual push the last one.

## Report

Write your report to `prompts/bridge-watcher-push-report.md` on this branch, in its own commit. The report must say:

- The base commit you verified (after fetch and rebase).
- The tests commit hash and the result (which tests failed first, as expected).
- The implementation commit hash and the result (full suite count, ruff, flake8).
- What the push step does, and what it does not do (no force-push, no rebase, no retry loop).
- Any file you created that was not in this prompt, and why.
- Any check that failed, and what you did about it.
- What stays Unknown.

Then commit locally and stop. Do not open a PR. Do not merge. Your push will be blocked; say so in your report.
