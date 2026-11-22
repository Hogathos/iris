# Relay timeout control (Silent tunnel cutoff)

## What it does
The dev panel lets a human change the silent tunnel cutoff while Iris
runs. The cutoff is how long a tunnel may sit silent, no bytes either
way, before Iris closes it. The panel shows the current cutoff and
takes a new one in seconds.

## Acceptance criteria
- A human can read the current cutoff on the panel index page.
- A human can set a new cutoff from the panel, 5 to 3600 seconds.
- Values outside 5 to 3600, and values that are not numbers, are
  refused: nothing changes, no row is written.
- A change takes effect for tunnels opened after it. Tunnels already
  running keep the cutoff they started with.
- Every accepted change writes one journal row first, saying the new
  cutoff.
- A tunnel closed by the cutoff reads "closed after {seconds}s silent"
  in the journal. The 30 second default does not change.
- Both test runners pass, lint and type check pass, CI is green.

## Non-goals
- No cutoff control in the consumer app. That is decided at app mode
  time.
- No cutoff per Run and no cutoff per destination. One cutoff for the
  whole proxy.
- No retiming of tunnels already running.
- No new default. The default stays 30 seconds.

## Decisions
- 2026-11-22, Charles: bounds are 5 to 3600 seconds. Rejected: no
  bounds. A fat-fingered 0.1 would cut every tunnel. Rejected: a 1
  second floor. Too low for slow model turns.
- 2026-11-22, Charles: a change bites on new tunnels only. Rejected:
  retiming live tunnels. That needs mid-relay deadline plumbing, out
  of scope.
- 2026-11-22, Charles: the fired row reads "closed after {seconds}s
  silent". The code name relay_timeout stays. Old rows keep their old
  value.
- 2026-11-22, Charles: every accepted change journals first, same
  pattern as the spend cap.
- 2026-11-22, Charles: dev panel only. Consumer settings wait for
  app mode.
