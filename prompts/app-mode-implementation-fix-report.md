# App-mode implementation fix report

Branch: `app-mode/implementation`. Three regressions fixed in
`devpanel/panel.py`. No merge. No pull request.

## Base commit

`git fetch origin` failed in this sandbox: `schannel:
AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`. The sandbox
holds no TLS credentials for github.com, so live GitHub main could
not be reached. Same block as the earlier implementation report.

What I verified locally instead: `614d8c2` is an ancestor of HEAD
(`git merge-base --is-ancestor` is true), so the branch sits on the
reviewer-verified base. No rebase performed. Whether GitHub main has
moved since is unverified from here. Charles should fetch and rebase
from his own machine before merging.

## Challenge the frame, once

A simpler fix was on the table: give the routes plain-string
defaults (`keep: str = ""`) so direct calls behave. That breaks the
real page: FastAPI would read `keep` from the query string instead
of the posted form, so pressing Keep it on would stop everything.
The chosen fix keeps the `Form` markers for HTTP and normalizes them
for direct calls. Said once, as asked. Done as the prompt says.

## Fix commit

`devpanel/panel.py` only. Two edits, both in the panel routes.

1. `_refused_request`: the default token moved from `""` to `None`,
   and the token check runs only when the caller passes a real
   string. GET routes pass nothing, so they get the Host check only.
2. `emergency_stop_execute`: the proxy-state check moved above the
   `keep` branch, and `keep` is normalized (`keep if isinstance(keep,
   str) else ""`). Only a real "keep it on" word keeps the stop.

## Dell verification

- `tests/test_appmode_prescriptive.py`: 18 passed.
- `ruff check .`: clean.
- `flake8 --select=E9,F63,F7,F82` on project files (venvs
  excluded): clean. The unexcluded run flags only
  `.venv/.../typing_extensions.py`, third-party files this change
  does not touch.
- Full suite: 232 passed, 68 failed, 74 errors, 6 subtests passed.
  Counts match the pre-change baseline exactly. Every failure is
  `WinError 10013` at socket bind time, plus `WinError 32` temp-file
  locks left by those bind failures. Environmental, not code.
- The three target tests fail on the Dell at `setUp`/start with
  `WinError 10013` before any assertion runs. That is the sandbox
  socket ban, not a verdict on the fix. Main must verify them in
  the VM.
- No-new-failure check: the ten socket-free panel test files give
  17 failed, 55 passed with the fix, and the identical 17 failed,
  55 passed on the pristine tree (tracked-only stash, then pop).
  The fix changes nothing runnable here.

## Failure 1: panel GET refused with 403

Root cause: the token check fired on GET requests. Every GET route
calls `_refused_request(request)`, and the old default `token=""`
meant `check_token("")` ran. Once `AppController.start` installs the
per-launch identity, `check_token("")` is False, so the index page
answered 403. The Host check was never the problem: the probe shows
`check_host("127.0.0.1:8199")` is True with identity installed,
while the same GET gate still answered 403. The prompt's Host-header
guess does not match the code. The spec agrees with the fix:
section 9 says the server checks the token on every POST, and the
Host header on every request.

Change: `_refused_request(request, token=None)`; the token branch
runs only for `isinstance(token, str)`. A POST with a missing or
stale token still gets 403. A GET gets the Host check only.

## Failure 2: Emergency stop leaves tunnels alive

Root cause: `emergency_stop_execute` never reached
`state.red_button()` on a direct call. The branch added
`keep: str = Form("")`, and the FastAPI marker object is truthy, so
`if keep:` took the "keep it on" branch and returned 303 without
cutting anything. The probe shows it: status 303 with zero
`red_button` calls before the fix, one call after. The proxy's own
`red_button` always cut live tunnels; the panel route just never
called it. This was a safety bug in the route, not in the proxy.

Change: normalize `keep` to a real string before the branch, so the
cut runs unless the turn-off page really sent "keep it on". An
explicit `keep="1"` still keeps (303, no cut); `keep=""` stops.

## Failure 3: watch-only route answers 303, not 503

Root cause: the same truthy `keep` marker. With `proxy_state=None`,
`if keep:` fired before the state check and returned the keep-page
303, masking the 503 the route should answer.

Change: the state check now runs before the `keep` branch, so 503
wins whenever there is no proxy state. The direct call with no state
answers 503 after the fix (303 before).

## Falsification

Each fix was probed the hostile way, not the confirming way: a Host
header with a port passes while a foreign Host still gets 403; a
tunnel-era `red_button` runs on execute while an explicit keep word
still keeps it in place; a no-state route answers 503 while a live
state answers 303 with the cut recorded. A POST with an empty token
under an installed identity still gets 403, so the token gate is not
weaker. Results: foreign host 403, empty-token POST 403, right-token
POST passes, `keep="1"` 303 with no cut, `keep=""` 303 with a cut.

## Files

One planned file changed: `devpanel/panel.py`. No file created. The
scratch probe (`probe_appmode_fix_scratch.py`) was deleted before
this report, so nothing unplanned remains.

## Failed checks

- `git fetch origin` blocked by the sandbox (`SEC_E_NO_CREDENTIALS`).
  Built on the verified local base and disclosed above.
- Full suite cannot go green here (socket listen forbidden). Baseline
  counts match exactly. CI and the VM must confirm.

## What stays Unknown

- VM verdict on the three socket tests for the exact fix commit.
- CI green for the exact fix commit (suite, ruff, flake8).
- Whether GitHub main moved since `614d8c2`.
- Push result (same sandbox network block as fetch may apply).
  Committed locally. The branch needs a hand push.
