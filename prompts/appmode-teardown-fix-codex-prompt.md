# App mode: explicit teardown of the embedded view before QApplication destruction (Codex)

## Challenge the frame

Before you start, if a simpler or sturdier way to fix this exists than what is described below, say so once, up front, then do what is asked. Challenge the frame, not just the details.

## Problem

Iris app mode embeds a QWebEngineView (a QWindow) inside the main
window with `QWidget.createWindowContainer(self.view, self)`
(`appmode/shell.py`, line 470). The parent is passed, so the
container is already a child window.

The `closeEvent` only stops the controller (proxy and panel). It
never destroys the view or the container. When the window closes,
both are left to Qt's automatic destruction. On Python 3.14 the app
segfaults during QApplication's automatic destruction, because the
QWindowContainer outlives the QWindow it hosts. This is upstream
PYSIDE-3529, filed Nov 23, 2026. The Qt for Python maintainer
(Friedemann Kleint) replied Nov 24: the container must be a child
window from the start, and the crash is in the automatic
destruction path (`QWindowContainer::toplevelAboutToBeDestroyed`).

## The fix

Destroy the embedded view and its container explicitly, before the
window closes, so QApplication's automatic destruction never touches
a live container.

In `IrisWindow.closeEvent` in `appmode/shell.py`, after stopping the
controller, explicitly tear down the embedded view in the right
order: remove and destroy the container (`self.view_host`) first,
then destroy the view (`self.view`). Reason out the destruction
order before writing code, and state the order in your report. The
order is the fix; a wrong order crashes the same way.

Do NOT change the `createWindowContainer` call itself. The parent is
already passed, and the flags default to `Qt.Widget`, so adding them
changes nothing. Do NOT touch the proxy or the panel. Do NOT change
how the window closes for the user.

## Tests first

Write the failing test first, before the fix.

The test must be subprocess-based. A segfault kills the test
process, so an in-process Qt test would kill the whole suite. The
test spawns a Python subprocess that builds the Iris window (or a
minimal equivalent with a QWindowContainer holding a QWindow),
closes it, and exits. It asserts the subprocess exit code is 0.
Before the fix, on Python 3.14, the subprocess segfaults (nonzero
exit). After the fix, it exits 0. Show the test failing before the
fix and passing after it.

Follow the existing test patterns in `tests/`. The test file is
`tests/test_appmode_teardown.py` (or the nearest existing app-mode
test file if one fits better; say which you chose and why).

## CI coverage, and what it does not cover

The subprocess test runs in CI on the Ubuntu Python 3.14 job, which
is where this crash reproduces. That is the CI verification.

Windows Python 3.14 is NOT in the CI matrix (Windows only runs
3.12 there), and Windows is the shipping platform and the hardest
teardown case. So CI does not verify the Windows 3.14 teardown.
Do NOT claim it does. After this merges, the fix must be verified
by hand on the Dell (Windows, Python 3.14): start the app, close
the window, confirm a clean exit. Say this in your report so the
reviewer does not forget it.

## Rules

- Repo rules in `AGENTS.md` apply: test-first, full suite green
  (baseline 455 passed, 51 subtests), `ruff check` and `ruff format`
  clean, pyright clean (0 errors). No flake8 — it was removed in
  PR #68; do not add it back or cite it.
- Teardown changes are platform-sensitive (see the PR #38 lessons
  in `AGENTS.md`: reason out the teardown order per platform first,
  satisfy the hardest platform first). If the fix needs a different
  order on Windows, say so in the report and in a code comment.
- Keep verbose comments. A comment that records why the teardown
  order is what it is is a decision record, not slop.
- Do NOT commit or push. You cannot — the sandbox is read-only for
  git. The watcher pushes the branch after your run. Do not merge,
  do not open a PR, do not touch any other branch.
- Use `activate.bat` or call `.\.venv314\Scripts\python.exe`
  directly for Python. Do not use a different interpreter.

## Report

Write your report to a file on this branch, in addition to
printing it in the terminal. The file is
`prompts/appmode-teardown-fix-codex-report.md`. The report
carries: the test results (new tests fail-before and pass-after,
full suite count), what changed, the teardown order you chose and
why, what you verified, and what is still open (including the Dell
Windows 3.14 hand check). Also report the token usage if available.
The reviewer decides whether the report file stays or is dropped
before merge.
