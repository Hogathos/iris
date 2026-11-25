# App-mode implementation fix prompt

Fix three regressions the review found on this branch. All three tests pass on main (`614d8c2`). All three fail on the test-merge of this branch into main. The full suite is 371 passed, 40 subtests passed, 3 failed. Ruff clean. Flake8 clean. Do not merge until these three pass.

## Read first

- The spec: `specs/app-mode/prescriptive-spec.md` (on this branch).
- The failing tests and the code they cover (below).
- The review report: `prompts/app-mode-implementation-report.md`.

## Challenge the frame, once

Before you write code, check once: if a simpler or sturdier way to fix these exists, say so in your report, then do what this prompt says. Do not nag it again.

## Git first

Run `git fetch origin` FIRST. Then rebase this branch onto `origin/main` if it has moved. Verify the base commit matches the current main on GitHub, and report the base commit in your report. Do not build on a stale base. Note: the branch base is already current main (`614d8c2`) as verified by the reviewer; only rebase if main has moved since.

## The three failures

These are the exact failures, as run by the reviewer in a Linux VM (not the Dell sandbox). Fix the code so they pass. The tests already exist; do not rewrite them to make them pass. If a test itself is wrong, say so in your report with the exact reason, and do not change it without saying so.

### 1. `tests/test_appmode_shell.py::ControllerTest::test_start_serves_the_panel_and_the_proxy`

Error: `AssertionError: 403 != 200` at `tests/test_appmode_shell.py:248`.

The test starts the controller, then does `GET /?tier=simple` on the panel port and expects 200 with `<h2>Runs</h2>`. It gets 403. Likely cause area: the F21 Host-header check. The test connects to `127.0.0.1`, so the Host header carries the port (`127.0.0.1:<port>`). If the check compares the Host header against bare `127.0.0.1` or `localhost` without accounting for the port, it refuses a legitimate local request. The token check is for POSTs only and should not fire on this GET. Find the real cause in the code; do not guess from this note alone.

### 2. `tests/test_devtool_buttons.py::DevtoolButtonsTest::test_emergency_stop_confirm_then_execute`

Error: `AssertionError: b'ping-after-cut' != b'' : tunnel still flows after cut` at `tests/test_devtool_buttons.py:164` (in `assert_tunnel_dead`), called from line 259.

The test opens a tunnel, confirms Emergency stop (200, correct words), executes it (303, correct), then checks the tunnel is dead. The tunnel still flows. Emergency stop must cut live tunnels, not just stop new calls. On main this test passes, so the rewrite lost the cut. Find where the old code closed live tunnels on Emergency stop and restore that behavior. This is a safety bug: the red button must stop what is already running, not only what comes next.

### 3. `tests/test_devtool_buttons.py::DevtoolButtonsTest::test_panel_routes_watch_only_without_proxy`

Error: `AssertionError: 303 != 503` at `tests/test_devtool_buttons.py:282`.

The test sets `proxy_state = None` and calls `redirect_button`, expecting 503 ("watching only"). It gets 303. Likely cause area: check ordering. A check that runs before the proxy-state check (the token check, or a redirect for a stale page) returns 303 and masks the 503 the route should return. The "watching only" 503 must win when there is no proxy state. Find the real cause in the code; do not guess from this note alone.

## Verification (read this carefully)

You cannot verify these three tests on the Dell. They need listening sockets, and the Dell sandbox forbids listening sockets (`WinError 10013`), so they fail there whether fixed or not. Do not use Dell test runs to judge these three.

What you CAN verify on the Dell, and must, before you commit:

- The 18 prescriptive tests in `tests/test_appmode_prescriptive.py` still pass.
- `ruff check .` clean.
- `flake8 --select=E9,F63,F7,F82 .` clean.
- No new test failures in the tests you can run.

Main verifies the three socket tests in the VM before any merge. If your fix is right, they pass there. If they still fail there, the fix goes back to you with the VM output.

## Checks

- Full suite on the Dell will still show the socket failures (WinError 10013). That is environmental, not your code. Report the Dell suite numbers and the VM-verified status separately.
- No unplanned files. If you create a file that was not in this prompt, say so in your report and why.
- Falsification: in your review of your own work, ask for a test that proves the fix wrong (a Host header with a port, a tunnel opened before Emergency stop, a route called with no proxy state), not one that confirms it is right.

## Git safeguards

Stage named files only. Never `git add -A`. Never `git commit -a`. Never commit `.exe`, `dist/`, `build/`, `*.db`, `.pytest_cache/`, `*.spec`, or secrets. Report the commit hash, the push result, and the test results, then stop.

Note: your own push from this run will be blocked by the sandbox (`SEC_E_NO_CREDENTIALS`). Commit locally and report. The branch will be pushed by hand.

## Report

Write your report to `prompts/app-mode-implementation-fix-report.md` on this branch, in its own commit. The report must say:

- The base commit you verified (after fetch and rebase, if any).
- The fix commit hash and what each fix changed.
- The Dell verification: the 18 prescriptive tests, ruff, flake8, and the Dell suite numbers.
- For each of the three failures: the root cause you found in the code, and the exact change that fixes it.
- Any file you created that was not in this prompt, and why.
- Any check that failed, and what you did about it.
- What stays Unknown.

Then commit locally and stop. Do not open a PR. Do not merge. Your push will be blocked; say so in your report.
