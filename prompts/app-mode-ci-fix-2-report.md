# App-mode CI fix 2: report

## Base

Built on `838d484` (branch `app-mode/implementation`, clean tree).
`git fetch origin` failed in the sandbox (schannel credentials error).
Local `origin/app-mode/implementation` equals HEAD.
Local `origin/main` is `8bab364`, possibly stale.
No rebase was possible. Report states this plainly.

## Frame challenge

Checked: no simpler legitimate path exists.
The test, the wording, and the CI config are all frozen.
The prescribed root-cause fixes are the way. One line, then done.

## macOS dialog

Root cause: `QMessageBox::setWindowTitle` shadows
`QWidget::setWindowTitle`. On macOS the shadow drops the title.
Qt documents this. The Qt source keeps the title off macOS builds
and discards it on macOS builds. The test reads the plain
`windowTitle()` property, so macOS returns `''`.
Linux and Windows forward the shadow to the plain setter,
so they pass.

Fix: `appmode/shell.py`, in `_emergency_off_dialog()`, calls
`QDialog.setWindowTitle(box, "Let agents work again?")`.
This sets the plain QWidget property at construction time.
Same words. Same look. No platform check.
The constructor-argument form would not fix it: the Qt
constructor calls the same shadow, so macOS would drop it too.
A local probe on PySide6 6.12 confirms the shadow is a distinct
method and the base-class call stores the title.
macOS proof waits on CI (build-quick).

## CodeQL: 36 alerts

No config change. No exclusion. No suppression.
All 36 use the prescribed technique.
The sandbox blocks the PR page, so each line below was read
in the code at `838d484` instead.

Shared fixes (each clears several alerts at once):
- `_tier_query` now escapes the tier with `_esc`
  (`html.escape`, standard library). Safe tiers render
  byte-identical. Unknown tiers (for example `bogus`) still
  pass through and still show everything.
- New `_local_target` validator: only a path on this panel
  passes (leading `/`, no scheme, no host, no backslash,
  no CR or LF). Anything else falls back to `/`.
- Every redirect target now uses fixed-prefix `+` form
  (for example `"/runs/" + trace_id + tierq`) inside
  `_local_target`. The user controls the suffix only, never
  the prefix. This is the form the CodeQL model accepts.
- `proxy.py` strips CR and LF from the trace id at ingress
  (line 1641). All 19 `X-Trace-Id` header writes downstream
  get the clean value.

XSS, all fixed (19):
- 1240: tier path fixed in `_tier_query`; `filter_query`
  now escapes the tier.
- 1589: `body` comes from `_run_detail_body`; its raw
  `tierq` links now escape through `_tier_query`.
- 1592, 1597: `selected=trace_id` was already `_esc`d in
  `_frame`; the tier path is fixed centrally.
- 1614: `_limits_body` has no raw renders (read fully);
  the tier path is fixed centrally.
- 1631: `_tools_body` escapes every value (read fully);
  the tier path is fixed centrally.
- 1640, 1666, 1695, 1732, 1853, 1895: raw `tierq` links,
  fixed centrally.
- 1705, 1741: added `_esc(trace_id)`; `tierq` fixed centrally.
- 1870, 1934, 1964, 2048, 2307: raw `_tier_query(tier)`
  links, fixed centrally.

Redirect, all fixed (15):
- 1669, 1698, 1735: `"/runs/" + trace_id + tierq` in
  `_local_target`.
- 1902, 1904: `"/" + tierq` in `_local_target`.
- 1966, 1984, 2002, 2020: `"/limits" + _tier_query(tier)`
  in `_local_target`.
- 2194, 2271: `"/" + _tier_query(tier)` in `_local_target`.
- 2212, 2233, 2252, 2273: `"/tools" + _tier_query(tier)`
  in `_local_target`.

Response splitting, both fixed (2):
- 1679, 1777: header values come from one variable, set at
  line 1641 from the `X-Trace-Id` request header. The ingress
  strip clears both sites and every sibling write.

Side effect on main: the central `_tier_query` escape also
clears main-branch XSS alerts at the same raw `tierq` links,
and the ingress strip also clears main-branch header writes.
Not counted. Not chased further.

## Tests and lints

Full suite on the Dell, with the fix:
68 failed, 232 passed, 74 errors, 6 subtests passed.
Full suite with the fix stashed (base `838d484`):
68 failed, 232 passed, 74 errors, 6 subtests passed.
The FAILED/ERROR name sets are byte-identical (diff empty).
Every failure is `PermissionError: [WinError 10013]` at
socket bind or listen. That is the known Dell network
lockdown, not a regression.

Spec tests for the touched code:
`test_appmode_prescriptive.py` plus `test_appmode_tiers.py`:
29 passed. This includes
`EmergencyStopTest::test_one_click_stops_everything`.
Helper probe: safe tiers byte-identical, hostile tier
escaped, local targets unchanged, remote and split targets
sent home, header strip verified.

Ruff: `ruff check` on project files is clean.
Flake8: the CI hard gate
(`--select=E9,F63,F7,F82`) reports 0 on project files.
The warnings pass shows only pre-existing style notes
(E501 under the default 79 columns; repo standard is 127).
No new lint note comes from this change.

## Still unknown

- macOS dialog proof: only CI runs macOS. The local probe
  plus Qt source plus docs is the evidence so far.
- CodeQL proof: CodeQL does not run on the Dell. Sanitizer
  choice was checked against the CodeQL query sources
  (`StringConcatAsSanitizer`, `ReplaceLineBreaksSanitizer`,
  `HtmlEscaping`). The CI check has the last word.
- `origin/main` may have moved; the fetch failed here,
  so rebase status is unverified. Push is left to hand.
