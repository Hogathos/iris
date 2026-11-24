# App-mode Connect button + agent-restart step

## What it does

The app window has a Connect button. It connects a known agent to
the Iris proxy and restarts the agent so the change takes effect.

## Success criteria

- The top bar has a Connect button beside Verify, Pause, and
  Emergency stop.
- Clicking Connect shows a picker with Claude Code, Cursor, and
  Continue.
- Picking an agent connects it to the Iris proxy. The dialog shows
  the plain-words result: which agent, what changed, where the
  backup is. It never shows the whole config, so no secret leaks.
- If the agent cannot be connected (no config file, already has a
  proxy, already connected, unknown agent, bad URL), the dialog
  shows the refusal in plain words and nothing changes.
- After a successful connect, the window warns the agent must
  restart and asks the user to confirm their work is saved before
  restarting. The warning has a button that reads "I'll save
  first", so the user knows to save their work first. The button
  does not save the agent's work itself. Iris cannot save from
  outside, and Iris cannot tell the agent to save. The proxy sees
  the agent's traffic. It does not control the agent. Clicking
  the button closes the dialog so the user can save in the agent,
  then restart. The dialog says in plain words that Iris cannot
  save the work, so the button never promises a save it cannot
  do.

## Restart warning: exact words

Title: "Restart {name}?"

Body: "{name} must restart so it picks up the change. Save your
work in {name} first. Iris cannot save your work for you."

Buttons: "I'll save first", "Restart", "Cancel".

"I'll save first" closes the dialog and restarts nothing. The
user saves in the agent, then restarts. "Restart" restarts the
agent now. "Cancel" closes the dialog and restarts nothing.
- On confirmation, Iris restarts the agent. On Windows, Iris closes
  Cursor and reopens it. For Claude Code, Continue, and Cursor on
  other systems, the dialog tells the user how to restart the
  agent. Iris cannot restart those from outside.
- Restart never happens without the warning and the confirmation.
- Closing the picker without picking an agent does nothing.

## Non-goals

- A Disconnect button.
- Muse Code as a connectable agent.
- Restart on macOS or Linux (manual instruction only in v1).
- Packaging, installer, first-run flow.

## Decisions

- 2026-11-23, Charles: Iris restarts the agent after a warning
  plus an unsaved-work check. v1: Cursor on Windows gets a real
  restart; Claude Code and Continue get a manual instruction.
  Chosen: Iris restarts. Rejected: the user restarts by hand.
  Why: the restart is friction, and Iris can do it safely if it
  checks first and warns. Nothing is lost.
- The button calls the existing one-click connect module. That
  module does not change.
