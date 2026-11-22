# Rate-limit clock (test seam)

## Problem

The allowlist/rate-limit bench test runs a burst proxy at 5 requests
per 1 second window. On a slow runner the burst can stretch past
1 second, the oldest stamp expires mid-burst, and the 6th request
gets through instead of 429. Windows 3.14 failed this way on PR #26
(run 45008119406): three failures, all the same count off by one.

## Fix

`ProxyState` takes a `rate_clock` callable, default `time.time`.
`check_rate` reads the window through it, nowhere else does. `serve`
passes it through. Production behavior is unchanged: the default is
the real clock.

The bench test gives the burst proxy a frozen clock. The window moves
only when the test advances it, so the burst counts are exact on any
runner. The three real sleeps become clock advances.

## Acceptance

- Full suite green, both runners, plus Ruff, flake8, Pyright clean.
- The bench test file passes three runs in a row.
- No change to any assertion or journal count in the bench test.

## Non-goals

- No change to the rate limit, the window, or any gate order.
- No frozen clock anywhere except the bench test's burst proxy.
- No change to the retry window, which still reads real time.

## Decisions

- 2026-11-22, Charles: frozen clock, not a wider window. A wider
  window only makes the flake rarer. The clock makes a failure a
  real bug, never timing.
