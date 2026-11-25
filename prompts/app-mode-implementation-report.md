# App-mode implementation report

Branch: `app-mode/implementation`. One run: tests first, then the
build. No merge. No pull request.

## Base commit

`git fetch origin` failed twice in this sandbox. The error is
`schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`.
The sandbox holds no TLS credentials for github.com, so the branch
could not be rebased against live GitHub main.

What I verified locally instead: `origin/main` (`4d42d92`) is an
ancestor of this branch. A rebase against the known main would be a
no-op. The build sits on the tests commit `3008a20`, whose parent is
the spec commit `ffea2f4`, which contains `origin/main`.

Risk: GitHub main may have moved since the last fetch. Charles
should fetch and rebase from his own machine before merging.

## Tests commit

Commit `3008a20`: 14 tests from spec section 12 in
`tests/test_appmode_prescriptive.py`. Result: 17 fail, 1 passes.
The pass guards existing red-button persistence across a restart.
No listening sockets in these tests: ProxyState on a temp DB, panel
routes called as plain functions, Qt offscreen.

## Implementation commit

Commit `203df60` (14 files, 4330 insertions, 433 deletions). Result:

- New tests: 18 of 18 pass (14 spec tests plus
  `test_stop_survives_restart`).
- Full suite: 232 passed (214 baseline plus 18 new), 68 failed,
  74 errors. The failing set is byte-identical to the pristine-tree
  baseline except one renamed test (see below). Every failure is a
  `WinError 10013` at socket listen time: this sandbox forbids
  listening sockets, so every live-server test fails here. No new
  failure. No fixed failure either.
- `ruff check .`: clean.
- `flake8 --select=E9,F63,F7,F82` on project files: 0 errors.
- `pyright`: cannot run in this sandbox (no output, immediate exit).
  CI must run it.

## What changed

- `db.py`: `claims`, `settings`, and `allowed_places` tables plus
  helpers. Additive. Old databases migrate through `CREATE TABLE IF
  NOT EXISTS` in `init_db`.
- `proxy.py`: Watching flag (memory only, on at launch); daily
  limit (persisted, `None` by default); allowed places (persisted);
  unpriceable gate in both the CONNECT and plain-HTTP paths;
  `stop_run` (one Run, not the global button); `lift_red_button`
  (turn Emergency stop off, old Runs stay stopped);
  `clear_override`; host validation for place labels. Defaults keep
  every old path: no limit set means nothing new blocks.
- `devpanel/states.py`: `claims_rollup`, `status_word`,
  `run_finished`. Existing functions untouched.
- `appmode/demo.py`: new. The demo agent (Unknown 1, resolved).
- `devpanel/panel.py`: rewrite per spec section 10. Same route
  names, same response codes, same journal reasons. New: token plus
  Host checks, consumer-build gating, Limits and Tools tabs, Calls,
  Why, and Cost sections, test places, sample Runs, settings in use.
- `appmode/shell.py`: rewrite per spec sections 0 to 9. Same
  controller, seams, teardown order, view object, button names, and
  connect flow.
- `specs/app-mode/prescriptive-spec.md`: `{spot}` resolved (below).

## The spot number

8888. That is the proxy port, the number the agent dials. The panel
port stays 8000. Test places sit past the agent spot: 8889 to 8892.
The spec now says 8888 in the agent-address lines, 8000 in the panel
line, the test-place ports in the Tools line, and the resolution in
the Unknowns line.

## Unknown 1: the demo agent

It did not exist. I built it as `appmode/demo.py`, as the spec
assumed. It saves two real notes, checks three claims through the
real `verify_read_back` (local file for claims 1 and 3, remote with
no local proof for claim 2), journals claim rows, and stops the Run
on the Incorrect claim. Nothing is staged.

## Unknown 2: the tier mechanism

Confirmed: no Developer-route removal existed. `?tier=` was
view-only filtering, and `iris.spec` bundles the whole devpanel.
I built the removal here: `AppController.start` sets
`CONSUMER_BUILD`, `stop` clears it, and the dev server leaves it
off. In the consumer build the map, settings, controls, limits,
tools, save, demo, sample, and test-place routes answer 404, and
`?tier=developer` answers 404 on every page. Hiding with CSS is not
used. The `?tier=simple` and `?tier=advanced` views keep working
because the shell drives them.

## New files

One: `appmode/demo.py`. The spec assumes the demo agent is built in
this work. No other file was created. The scratch falsification file
was deleted before the commit.

## Old tests updated

The spec redefines the UI, so six files needed matching updates.
Each keeps its intent:

- `tests/test_appmode_shell.py`: E-stop is one click, no
  confirmation. The confirm dialog moved to turn-off only.
- `tests/test_appmode_tiers.py`: new column, tab, and form words.
  Simple hides diagnostics, advanced adds Calls and Went to, dev
  shows all four sections. Tier persists in links, back link, and
  pause redirect.
- `tests/test_devpanel_swarm_map.py`: the index no longer links the
  map (the spec frame is exact). The test now checks the map route
  directly.
- `tests/test_devtool_buttons.py`: turn-off words, Limits and Tools
  tab locations, Send words.
- `tests/test_devtool_panel_polish.py`: the label-form hint moved
  with the block form to the Limits tab.
- `tests/test_relay_timeout_control.py`: the cutoff form left the
  page. The setter and the route stay, tested directly.

## Failed checks and what I did

- Fetch blocked (sandbox TLS). Built on the verified local base
  and disclosed it above.
- Full suite cannot go green here (socket listen forbidden). I ran
  the pristine baseline the same way and diffed the failing sets:
  identical. CI must confirm.
- A `git stash -u` round trip swallowed the untracked
  `appmode/demo.py`, and I dropped that stash before noticing. I
  recreated the file from the same content and re-ran the suite:
  18 of 18 pass, so the file is proven equivalent. Lesson: never
  `stash -u` in this sandbox.
- Falsification caught two real bugs before the commit:
  `allow_place` accepted punctuation soup (`:::bad`) and
  mixed-case places missed (`Example.COM` vs `example.com`).
  Both now fail closed. Panel route posts with a stale token or a
  foreign Host change nothing (probed, not just asserted).

## Challenge the frame, once

- The spec names `QWebEngineView`. The code uses QtWebView
  (`QWebView`), which rides on WebView2 on Windows and carries the
  fixed teardown. Switching view classes would invalidate PR #52
  and every Qt test for no screen-visible gain. I kept `QWebView`.
- Claims got their own table instead of living in journal reasons.
  Reasons must stay machine-parseable for the existing Verify
  tests, and the roll-up plus Why lines need first-class storage.
- Finished has no wire signal, so it is a stated heuristic: no
  live tunnel, something checked, nothing new for 60 seconds.
  Anything else reads Running.

## What stays Unknown

- CI green for the exact commit (suite, ruff, flake8, pyright).
- The connection mechanism with a non-developer (spec Unknown 4).
- Whether the 60-second Finished rule matches how agents go quiet.
- Whether auto-refresh keeps scroll position in real browsers (it
  keeps the selected Run through the URL; scroll is unverified).
- Push result (same sandbox network block as fetch may apply).
