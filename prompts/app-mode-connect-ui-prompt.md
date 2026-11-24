# App-mode Connect button + agent-restart step (full-context test)

## Task

Implement the Connect button plus the agent-restart step in the app
window, per the spec. Make all 13 failing tests pass. Keep the full
suite green. Report, push the branch, stop.

This is a full-context run. Read every file named below before you
write code. Do not work from a short summary. The spec, the working
rules, the decisions, and the tests are the context. They are all in
this repo, on this branch.

## Read first, in this order

1. `specs/app-mode-connect-ui/requirements.md` — the spec. What the
   button does, its success criteria, its non-goals, its decisions,
   and the exact words of the restart warning dialog. Follow the
   spec. The dialog text in the spec is exact. Use those words and
   no others.
2. `tests/test_appmode_connect_ui.py` — the 13 failing tests. They
   are the contract. Make them pass. Do not change the tests.
3. `AGENTS.md` — the repo working rules. They win over this prompt
   unless Charles says otherwise in words. Pay attention to: plan
   first is done (the plan is filed, Charles approved it); write
   the failing test first is done (the tests are here); the review
   pass names specific risks; keep verbose comments; stage named
   files only; never commit `.exe`, `dist/`, `build/`, `*.db`,
   `.pytest_cache/`, `*.spec`, or secrets; work on a branch, open
   a pull request, merge only after CI is green and Charles
   approves in words; run pytest only (Charles decided Nov 23 to
   drop the separate unittest runner; pytest collects
   unittest.TestCase since 9.0, so one pytest run covers both);
   never quote a test count from memory; check CI for the exact
   commit.
4. `specs/app-mode-connect/requirements.md` — the one-click connect
   spec. It defines the connect module you call. Read its Decisions
   section. The button calls the existing `connect_agent` and
   `disconnect_agent`. That module does not change.
5. `appmode/shell.py` — the window you change. Read the whole file.
   Note the top bar (Verify, Pause, Emergency stop, Advanced
   checkbox), the view hosted by `createWindowContainer`, the
   `_LiveSeams` pattern, and the injected `ask_words` and
   `ask_confirm` callables. The Connect button follows the same
   pattern: injectable callables for the picker, the message
   dialog, the restart warning, the connect call, and the restart
   call, so tests can stub them.
6. `appmode/connect.py` — the connect module you call. Read the
   whole file. Note `connect_agent`, `disconnect_agent`, `Refused`,
   `config_path`, and the plain-words result strings. The result
   names the proxy fields only, never the whole config, so no
   secret leaks. The dialog shows that result, not the config.
7. `tests/test_appmode_shell.py` — the window test patterns. Note
   the offscreen setup, the one QApplication, `_delete_window`,
   `tearDownModule`, the stub controller, and the recording seams.
   The new test file follows the same patterns.

## Decisions (already made, do not re-decide)

- 2026-11-23, Charles: Iris restarts the agent after a warning
  plus an unsaved-work check. v1: Cursor on Windows gets a real
  restart; Claude Code and Continue get a manual instruction.
  (This is Option 1. It is decided. Do not offer Option 2.)
- 2026-11-23, Charles: the restart warning has a button that
  reads "I'll save first". It does not save the agent's work
  itself. Iris cannot save from outside, and Iris cannot tell the
  agent to save. The proxy sees the agent's traffic. It does not
  control the agent. Clicking the button closes the dialog so the
  user can save in the agent, then restart. The dialog says in
  plain words that Iris cannot save the work, so the button never
  promises a save it cannot do.
- The restart warning dialog text is exact, from the spec. Title:
  "Restart {name}?" Body: "{name} must restart so it picks up the
  change. Save your work in {name} first. Iris cannot save your
  work for you." Buttons: "I'll save first", "Restart", "Cancel".
- The button calls the existing one-click connect module. That
  module does not change.

## Files to change

- `appmode/shell.py` — the window: Connect button, picker dialog,
  result dialog, restart warning dialog, wiring to seams.
- `appmode/restart.py` — new. `restart_agent` per agent and OS.
- `tests/test_appmode_connect_ui.py` — already written. Do not
  change it. Make it pass.
- `specs/app-mode-connect-ui/requirements.md` — already written.
  Do not change it.
- `appmode/connect.py` — no change.

## Do it

1. Read every file named above.
2. Implement `appmode/restart.py` and the window changes in
   `appmode/shell.py` so the 13 tests pass.
3. Run the full suite with pytest, plus ruff, flake8, and
   pyright. All must pass. Do not quote a test count from memory.
   Run the suite and read the result.
4. Run the new test file on its own first, then the full suite.
   Confirm the 13 new tests pass and nothing else broke.
5. Commit the changed files only (named files, never `git add -A`).
   Push the branch `app-mode/connect-ui`. Do not open a pull
   request. Do not merge. Charles approves the merge in words.
6. Report: the commit hash, the push result, the test results
   (pytest, lint results), and what is still open. Then
   stop.

## Review pass (do this before you report)

Name the specific risks, then check each one. Do not write a
generic review.

- Falsification: write a test that proves the implementation wrong,
  then see if it fails. Boundary, invalid input, partial failure.
  A review that only confirms what is already believed adds
  nothing.
- Unplanned files: did the change create any file that was not in
  the plan? Did it split existing code into a new file that was
  not planned? A new file that was in the plan is fine.
- Security: does the dialog show the whole config anywhere? It
  must show only the proxy fields from the connect result, never
  the whole config, so no secret leaks.
- Edge cases: the picker is closed without picking; the agent has
  no config file; the agent already has a proxy; a backup already
  exists; the restart is declined; the Save button is picked.
  Each must leave the config unchanged and the agent unrestarted.
- Efficiency: spot unnecessary, redundant, or over-processing code.
  Steer the code toward high performance.
