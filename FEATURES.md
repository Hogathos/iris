# Iris — what is built, what is planned

Short version, for a fresh reader. No code reading needed.
Checked against main 68252ed, CI run 82 green, 94 tests, 2026-11-17.

## What Iris is, one sentence

Iris watches AI agents from the outside, checks what they said they
did against what actually happened, and stops the run when it does
not match, until a human checks it.

## Built

### Watch — the proxy

- Local proxy on 127.0.0.1:8888. Loopback allowlist only.
- A request to a non-allowlisted destination gets a 403 and is never
  forwarded.
- Rate limit 100 requests per 10 seconds per destination. Over the
  limit gets a 429, and it recovers after the window.
- CONNECT tunnels are a raw byte relay. Iris counts bytes, never
  looks inside, no TLS decryption, no MITM.
- Metadata only. No request or response bodies, no API keys, no
  header values in the log.
- Every attempt is a row in a SQLite file, `attempts.db`, written
  before the call goes out. Trace ID is preserved if one arrives,
  never injected downstream.
- Outcome accounting reconciles: allowed plus blocked plus
  rate-limited plus paused plus redirected plus stopped plus
  verified plus warned plus other equals total attempts.

### Steer — stop and redirect, human only

- Pause one run. Cuts that run only, journal first, until a human
  checks it and says it can go again.
- Redirect one run. Moves one run to a destination a human already
  allowed. If the new place is not allowed, Iris refuses, and the
  run stays where it was.
- Emergency stop. Stops all runs at once. Human only.
- Per-network overrides. Different rules per network.
- Spend caps. Warn at 80 percent, block at 100 percent.

### Verify — read back, in plain words

- Local file and SQLite only, first version. Iris reads the real
  effect back after close and fsync, or after COMMIT, and says
  Correct, Incorrect, or Unverified.
- Correct means the read proves it. Incorrect means the read proves
  it did not happen, and the run pauses. Unverified means Iris
  could not check it, so a human has to look.
- No scores as verdicts. No LLM as judge.

### Cost

- Blocked, rate-limited, paused, redirected, stopped, and verified
  runs are $0.00. Loopback is $0.00. A nonzero recorded cost
  always wins. Everything else is Unverified, never guessed.

### Dev tool, not the consumer app

- A dev-only panel at 127.0.0.1:8000, with Pause and WARNED and
  BLOCKED states. Dev only, not the consumer app.

### Live runs

- Second live workflow, Nov 16: GET to A, then POST to B, both
  legs Correct, non-allowlisted exfil refused 403, cost $0.00.

## Planned, not built

- App mode. One double-clickable .exe, dashboard embedded in the
  Iris window, not in a browser, dev tools excluded.
- Verify, next profiles, one at a time: S3, Postgres primary,
  Stripe direct GET, then GitHub, Slack, Gmail, Notion, public web.
- Usage ledger. Per-model money and token use, and a list of
  costly requests per day, with a limit the user sets. Not built
  yet, so no per-model money chart exists yet.
- Notifications for incidents the user defines. Local first, then
  email or text for critical ones. Not built.
- Incident severity levels and an incident list. Not built.
- Agent-optimized report. One structured, machine-readable digest
  a watcher agent can read cheap, on a schedule, and flag. The
  watcher can flag, it cannot clear. Only a human clears a run.
  Planned, not built.
- Full logs, documented format. Rows are on disk in `attempts.db`
  and can be searched with SQLite tools. The format still needs
  documenting in the repo. A hash-chained, sealed evidence pack,
  so a change shows, is planned, not built.
- Cage, Stage 1. A disposable sandbox the agent works inside,
  egress Iris only, promotion text and source only, scoped short
  lived tokens, Iris watches by diff from outside. Pick is Docker
  Sandboxes, sbx, 0.38.0 or higher, fallback Microsandbox. Linux
  CLI agents only. Not built.
- Cage, Stage 2. A daemon in the cage as a dead-man's switch,
  heartbeat to a watchdog outside, plus a host-side process
  watcher for crashes and spawn storms. Flag only, diff still
  wins, human still decides. Not built.
- Mars. A red-team harness that attacks Iris in the lab, and an
  Iris vs Mars mode for demo. Not built.
- TLS, MCP, TCP, UDP, and gateway support. Measure. Automatic
  vendor price fetch. Not built.

## What Iris is not

- Not a firewall, not an IDS, not an EDR, not a network monitor.
- Not a mind reader. It cannot see inside an agent, only what
  the agent actually does.
- Not protection against a hostile agent on the same machine.
  A proxy alone cannot stop an agent that goes around it. Only
  OS-enforced tiers hold there, and that is what the Cage is for.
- It cannot detect a bypass from silence. If there is no trace,
  Iris says Unverified, and a human investigates.
- It does not solve the Hugging Face incident. It makes that
  class of failure checkable, where Iris is deployed.

## Where it lives

Iris lives in `agent-observability-proxy/` in this repo.
The repo also holds other, unrelated projects, see below.
