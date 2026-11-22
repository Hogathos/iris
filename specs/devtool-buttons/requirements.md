# Devtool buttons

Four panel controls for the dev-only control panel. Every setter
already exists in the proxy. No proxy change. Panel work only.

## What it does

1. Redirect one Run. Form on the Run page asks for the new
   destination. Calls `redirect_run`. Allowlisted loopback only.
   Anything else is refused and the Run stays where it was.
2. Spend cap. Form on the index asks for a destination and dollars.
   Calls `set_cap`. Warns at 80 percent, blocks at 100 percent.
3. Block this network. Form on the index asks for a destination.
   Calls `set_override`, pause action only. Allow, block, and
   redirect are refused by the setter. Only pause is built.
4. Emergency stop. One button on the index. Calls `red_button`.
   Stops every Run. It gets a confirm page first: GET shows the
   page, POST stops everything.

Each route mirrors the Pause button shape: 503 when the panel is
watching only, 303 on success, a refused page on a refused value.

## Looks

Small styling pass, same change. One style block, clear sections
(Runs, Controls, Emergency stop). Clean and readable, nothing
fancy, nothing that hurts to look at. No React. Plain words only.

## Acceptance

- All four routes exist and behave as above.
- The index shows the cap form, the block form, and the Emergency
  stop section. The Run page shows the redirect form.
- The confirm page comes before any stop. No one-click stop.
- Full suite green, both runners. Ruff 0. flake8 0 on CI select.
  Pyright 0.

## Non-goals

- The 1e devpanel flip. The override cut-loop fix. Unifying the
  panel words with the API words. The cleanup and refactor, that
  comes after, once every button exists. The consumer app. Any
  auto-control.

## Decisions

- Buttons first, cleanup after (2026-11-22, Charles, frame
  challenge). Cleaning first means cleaning twice: the buttons
  copy the Pause-button shape, so one cleanup after unifies all.
- Emergency stop confirm page (2026-11-22, proposed, no veto).
- Styling stays small and readable (2026-11-22, Charles: good
  looking for what it is, not too important, must not hurt the
  eyes or be unreadable as a tool).
- One-go run (2026-11-22, Charles: "All of it, until you need
  me"). Tests written first inside the run, review after.
