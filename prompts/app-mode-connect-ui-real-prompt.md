# App mode: Connect button for a real agent (turn the demo into the real app)

## Base and frame challenge

Fetch origin FIRST. Rebase onto `origin/main` if it has moved since your last fetch, and report the base commit you built on. Do not assume the base. Verify it and report it.

Challenge the frame before you start: if a simpler or sturdier way to make app mode real than the fix below exists, say so once, up front, in your report, then do the fix as written. Do not nag the point. Silence means you checked and this is the best path.

## The problem

App mode is on main, but the only way to see a run in it is the demo (`appmode/demo.py`), a scripted agent. The real path — a real agent connected through Iris — is not wired into the shell. The connect code (`appmode/connect.py`) and the restart code (`appmode/restart.py`) already exist from the one-click connect work, but there is no Connect button in the consumer shell that uses them, and no handling of the agent restart step.

The demo was a stand-in to prove the shell works. Turn it into the real app: the shell must let the user connect a real agent and see its real traffic.

## The fix

Read the prescriptive spec (`specs/app-mode/prescriptive-spec.md`) and the existing code (`appmode/shell.py`, `appmode/connect.py`, `appmode/restart.py`, `appmode/demo.py`, and the connect tests) before changing anything. Follow the spec's wording and layout. Do not invent new wording.

Add a Connect button to the consumer shell that:
- Uses the existing `appmode/connect.py` one-click connect to connect a real agent (Muse Code, Cursor, Continue — whichever the connect code supports). Do not re-implement connect. Do not duplicate the connect logic in the shell.
- Handles the agent restart step. The agent only reads the proxy setting at startup, so after connecting, the agent must be restarted before its traffic flows through Iris. The shell must make this plain to the user in the spec's wording and give them the restart step (use the existing `appmode/restart.py` where it fits; do not invent a new restart mechanism).
- After the restart, the shell shows the real agent's runs the same way it shows the demo runs. The run table, the Verify gate, and the cost display do not change.

Keep the demo as a fallback. The demo stays working exactly as it is, for seeing app mode without a real agent connected. Do not remove it and do not change its behaviour. The Connect button is the real path; the demo is the stand-in.

## Tests

Test-first. Write failing tests first, then the implementation. Cover:
- The Connect button is present in the consumer shell and wired to the existing connect code (not a duplicate implementation).
- After connect, the shell shows the restart step to the user (the wording matches the spec; the agent is not assumed to be live until restarted).
- After the restart, a real agent's run appears in the run table the same way a demo run does.
- The demo still works unchanged when no real agent is connected.

## What not to do

- Do not change the prescriptive spec. Do not change the spec's wording or layout. If the spec does not cover a piece of wording you need, name it in the report as Unknown and pick the plainest wording, do not invent spec language.
- Do not re-implement connect or restart. Use the existing modules.
- Do not remove or change the demo's behaviour.
- Do not push. The sandbox blocks the push. Commit locally and report. The branch is pushed by hand.
- Do not merge. Do not open a PR. This is a feature on its own branch.
- Touch only the app-mode code and its tests. No spec file changes, no workflow changes.

## Verification

- The new tests pass, and the full suite is green in the VM sense (the Dell socket tests fail at bind, WinError 10013 — environmental, not a regression; report the counts and confirm the failure set is identical with the change stashed).
- `ruff check .` clean. `flake8` clean on the touched files. Pyright 0 errors.
- Falsification: the tests must attack the fix (a connect that must not pretend the agent is live before restart, a restart step that must not be skipped, a demo that must not break when the real path is added), not just confirm the happy path.

## Report

Write your report to `prompts/app-mode-connect-ui-real-report.md`, own commit. It must state: the base commit you built on; the frame challenge (what you checked, one line); what the Connect button does and where it lives; how the restart step is shown and handled; what the demo still does; the test results (new tests, full suite with stashed baseline); ruff, flake8, and Pyright results; and what is still unknown.
