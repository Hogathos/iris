# App-mode implementation prompt

Implement the Iris app mode: the consumer app look AND the devpanel look, exactly as the prescriptive spec says. No design choices left open. The spec pins every word, every layout, every behavior.

## Read first

- The spec: `specs/app-mode/prescriptive-spec.md` (on this branch). It is built from the deslopified scratch paper. If a line in the spec conflicts with the designs, the spec wins.
- The real code it describes: `appmode/shell.py`, `devpanel/panel.py`.

## Challenge the frame, once

Before you write code, check once: if a simpler or sturdier way to do this exists, say so in your report, then do what the spec says. Do not nag it again.

## Git first

Run `git fetch origin` FIRST. Then rebase this branch onto `origin/main`. Verify the base commit matches the current main on GitHub, and report the new base commit in your report. Do not build on a stale base.

## Test first

Write the failing tests first. Section 12 of the spec lists 14 tests. Write them, run them, show they fail. Commit them on their own commit. Charles reviews the tests before the implementation, so the tests commit must be clean and readable on its own. Then implement. New behavior only; refactors skip the test-first step.

## Implement

Both parts, in one run:

1. **Consumer app look.** The native PySide6 shell in `appmode/shell.py`: first-run screen, dashboard, menus, toolbar, run table, run detail, notice strip, status bar, demo, Verify gate, Pause, Emergency stop, More detail switch, Settings, Limits, errors. Everything in sections 0-9 of the spec.
2. **Devpanel look.** The developer panel in `devpanel/panel.py`: the look rules, the page frame, the Runs/Limits/Tools tabs, the Calls/Why/Cost tabs, the controls, the errors. Everything in section 10 of the spec.

Rules that bind the whole run:

- Same strings in the app and the devpanel wherever the same thing appears. Those strings must stay identical in both.
- Verdicts are Correct, Incorrect, Unverified on screen. "Unknown" never appears on screen.
- A Run is labelled "Run " plus the first 8 characters of its trace id. No sequential number.
- Verify is the gate: the human types what was checked, in plain words, and stamps approval. The claim-checker is Part 2, queued, not in this work.
- The word is "journal", not "record".
- Loopback security (F21) is a requirement: per-launch token on every action, in memory only, plus Host-header check accepting only 127.0.0.1 and localhost with the panel's own port. No setting turns either off.
- Spending limit: when a limit is set, Iris blocks calls it cannot price, unless the human allowed that destination. Allowing a place is one click, in context, with Undo after.
- The consumer build has no Developer pages or routes. Hiding them with CSS is not enough.
- The demo agent really saves notes and Iris really reads them back. A staged verdict would teach the wrong thing. Build the demo agent as part of this work if it does not exist yet (spec section 13, Unknown 1).
- The exact loopback spot number is a `{spot}` placeholder in the spec. Pick the number, and update the spec with it.
- Confirm whether the `?tier=` mechanism already hides Developer routes in the consumer build, or whether that removal must be built here (spec section 13, Unknown 2). Report what you found.

## Checks

- Full test suite green, plus the 14 new tests.
- `ruff check .` clean. `flake8 --select=E9,F63,F7,F82 .` clean.
- No unplanned files. If you create a file that was not in the spec, say so in your report and why.
- Falsification: in your review of your own work, ask for a test that proves the implementation wrong (boundary, invalid input, partial failure), not one that confirms it is right.

## Git safeguards

Stage named files only. Never `git add -A`. Never `git commit -a`. Never commit `.exe`, `dist/`, `build/`, `*.db`, `.pytest_cache/`, `*.spec`, or secrets. The checked-in `iris.spec` is the one deliberate exception and you are not touching it. Report the commit hash, the push result, and the test results, then stop.

## Report

Write your report to `prompts/app-mode-implementation-report.md` on this branch, in its own commit. The report must say:

- The base commit you verified (after fetch and rebase).
- The tests commit hash and the result (which tests failed first, as expected).
- The implementation commit hash and the result (full suite count, ruff, flake8).
- The loopback spot number you picked, and where you put it in the spec.
- What you found on the tier mechanism (Unknown 2) and on the demo agent (Unknown 1).
- Any file you created that was not in the spec, and why.
- Any check that failed, and what you did about it.
- What stays Unknown.

Then push the branch and stop. Do not open a PR. Do not merge.
