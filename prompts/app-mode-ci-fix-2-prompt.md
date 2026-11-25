# App-mode CI fix 2 — macOS dialog title + CodeQL (PR #61)

## Base and frame challenge

Fetch origin FIRST. Rebase onto `origin/main` if it has moved since your last fetch, and report the base commit you built on. Do not assume the base. Verify it and report it.

Challenge the frame before you start: if a simpler or sturdier way to make CI green exists than the two fixes below, say so once, up front, in your report, then do the fixes as written. Do not nag the point. Silence means you checked and this is the best path.

## What is failing

PR #61 (branch `app-mode/implementation`, head `838d484`) has two CI failures left. Everything else is green: Pyright 0 errors, ruff clean, flake8 clean, full suite green on Ubuntu (3.10, 3.12, 3.14) and Windows (3.12). Do not touch Pyright, ruff, or flake8. Do not change any test. Do not weaken any config. No blanket ignores.

### 1. macOS build-quick failure — one test

`tests/test_appmode_prescriptive.py::EmergencyStopTest::test_one_click_stops_everything`, line 380:

```
self.assertEqual(dialog.windowTitle(), "Let agents work again?")
AssertionError: '' != 'Let agents work again?'
```

`dialog` is `window._emergency_off_dialog()`. On macOS (3.12) only, its window title is empty. The same test passes on Ubuntu (3.10, 3.12, 3.14), on Windows (3.12), and in the Linux VM. The title "Let agents work again?" is the prescribed wording. Do not change the test and do not change the wording.

Fix the dialog so its window title is set reliably on all platforms, macOS included. The likely cause is a macOS Qt behaviour where the title is not applied until the dialog is shown, or where the title is set in a code path macOS ignores. Find the real cause by reading `_emergency_off_dialog()` and how the dialog is constructed in `appmode/shell.py`. Set the title at construction time in a way that holds on macOS. Do not add a macOS-only workaround that hides a real bug. Do not change the dialog's wording, layout, or behaviour on other platforms.

Acceptance: `tests/test_appmode_prescriptive.py::EmergencyStopTest::test_one_click_stops_everything` passes. Run the full suite; it must be green on the Dell (the socket tests fail there at bind, WinError 10013 — that is environmental, not a regression; report the counts and confirm the failure set is identical with the fix stashed).

### 2. CodeQL — 36 alerts, all in PR-changed files

The CodeQL check on PR #61 fails with 36 new alerts (19 High, 17 Medium). All 36 are in files this PR changed. None are in `appmode/shell.py`, `appmode/demo.py`, `devpanel/states.py`, `db.py`, tests, prompts, or specs.

**`devpanel/panel.py` — 34 alerts**
- High, Reflected server-side cross-site scripting ("Cross-site scripting vulnerability due to a user-provided value"): lines 1240, 1589, 1592, 1597, 1614, 1631, 1640, 1666, 1695, 1705, 1732, 1741, 1853, 1870, 1895, 1934, 1964, 2048, 2307 (19 alerts).
- Medium, URL redirection from remote source ("Untrusted URL redirection depends on a user-provided value"): lines 1669, 1698, 1735, 1902, 1904, 1966, 1984, 2002, 2020, 2194, 2212, 2233, 2252, 2271, 2273 (15 alerts).

**`proxy.py` — 2 alerts**
- Medium, HTTP Response Splitting ("This HTTP header is constructed from a user-provided value"): lines 1679, 1777.

Read each alert on the PR before fixing it. Fix the real ones in the new code:
- XSS: HTML-escape user-provided values before they are rendered into panel HTML. Use the standard library escaping, not a hand-rolled replace. The panel must keep showing the same values, only escaped.
- Redirect: validate any redirect target built from a user-provided value. Only allow a local or relative target. Reject anything else.
- Response splitting: strip or encode CR and LF in any value placed into an HTTP header.

Do not weaken the CodeQL config. Do not exclude files. Do not add blanket `# codeql[query]` ignores. A narrow suppression with a reason comment is allowed only for a single alert you can prove is a false positive for this local-only tool, and you must name it in the report with the proof. The panel and the proxy are loopback-only and serve the developer's own trace data, so the real-world risk is low — but the check must go green, and a security product must not ship reflected XSS in its own panel.

Note: the main branch already carries similar alerts in these files (34 open). This fix is about the 36 the PR adds. Do not try to fix all of main's alerts in this run. Fix the 36 named above. If a fix for one of the 36 also clears a main-branch alert, say so in the report.

Acceptance: the 36 alerts named above are gone or individually justified in the report. Run the full suite after the fix; it must be green on the Dell (same socket-test caveat as above). Run ruff and flake8; both must be clean.

## What not to do

- Do not change any test file. The prescriptive tests are the spec.
- Do not change `pyrightconfig.json`, the CodeQL workflow, or any CI workflow.
- Do not change the emergency-off dialog wording ("Let agents work again?") or the panel wording.
- Do not push. The sandbox blocks the push. Commit locally and report. The branch is pushed by hand.
- Do not merge. Do not open a PR. This is a fix on the existing branch.

## Report

Write your report to `prompts/app-mode-ci-fix-2-report.md`, own commit. It must state: the base commit you built on; the frame challenge (what you checked, one line); for the macOS fix, the root cause you found and the fix; for CodeQL, each of the 36 alerts and whether it was fixed or justified (with proof for any justification); the full test-suite counts with and without the fix (stashed baseline); ruff and flake8 results; and what is still unknown (notably, macOS verification is via CI, and CodeQL verification is via CI, since neither runs on the Dell).
