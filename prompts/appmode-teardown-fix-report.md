# App mode teardown fix: report

## Result

Done. The window now destroys the embedded view before Qt does.
No merge. No PR. Branch only.

## Commits

- Tests: 2b578d2. File `tests/test_appmode_teardown.py`.
- Fix: af07783. File `appmode/shell.py`.
- This report is its own commit on the same branch.

## Test results

- New test before the fix: FAIL. Child exit 1.
  Out: `TEARDOWN-FAIL: view_host still alive after close; view
  still alive after close`.
- New test after the fix: PASS. 1 passed.
- App mode window tests: 16 passed
  (`test_appmode_shell.py` plus the new test).
- Full suite: 325 passed, 40 subtests passed. Zero failures.
- Ruff (E9, F63, F7, F82): clean.
- Flake8 (E9, F63, F7, F82): clean, 0 findings.
- Pyright: 0 errors, 0 warnings.
  Note: run with the venv interpreter
  (`python -m pyright --pythonpath .venv/Scripts/python.exe`).
  A bare run reports missing PySide6 imports because it uses the
  wrong interpreter. The errors are environment noise, not code.

## What changed

`IrisWindow.closeEvent` in `appmode/shell.py` did one thing: it
stopped the controller. It left the view and the container alive.
Qt destroyed them later, in the wrong order, and the app
segfaulted on Python 3.14 (upstream PYSIDE-3529).

It now does three things, in this order:

1. Stop the controller (unchanged).
2. Remove `view_host` from the layout and delete it.
3. Delete `view`.

Nothing else changed. The `createWindowContainer` call is
untouched. The proxy and the panel are untouched. The window
closes for the user exactly as before.

## Teardown order and why

Container first, then the view. Never the reverse.

The crash is the container outliving its window
(`QWindowContainer::toplevelAboutToBeDestroyed`). The container
holds the only reference to the window surface. Deleting the view
first leaves the container over a dead window. That is the same
crash. Deleting the container first unhooks the wrapper while the
window still lives. Then the view dies with no wrapper over it.

Deletion is synchronous (`shiboken6.delete`) on every platform.
A posted delete (`deleteLater`) leaves the native surface alive
until the loop pumps. The loop never pumps during close on
Windows. The surface then dies in automatic destruction. That is
the crash again. Windows is the hardest case, so it sets the rule
for all platforms. No platform needs a different order.

The guards (`getattr`, `isValid`) make a second close safe. A
half-built window also closes safe.

## Test file choice

New file `tests/test_appmode_teardown.py`, not an addition to
`tests/test_appmode_shell.py`. That module owns one in-process
QApplication with its own module teardown. A subprocess test
touches no in-process Qt state, so it keeps that teardown
undisturbed.

The child builds a real `IrisWindow` with a stub controller. It
closes the window, checks both objects are dead, cleans up, and
exits. The parent asserts exit code 0. Before the fix the child
exits 1. On Python 3.14 it can also segfault, which is also
nonzero. After the fix it exits 0 and prints `TEARDOWN-OK`.

## What was verified

- Fail-before and pass-after of the new test on this machine
  (Windows, Python 3.13, PySide6 6.12).
- Full suite green after the fix.
- Lint and type gates clean.
- Real-behavior checks: `close()` still stops the controller
  (`test_close_stops_the_controller` passes). Layout removal runs
  before deletion. No other code touches `view_host` or `view`.

## Still open

Hand check on the Dell (Windows, Python 3.14): start the app,
close the window, confirm a clean exit. CI does not cover this.
The Ubuntu Python 3.14 job runs the subprocess test, which is the
CI verification. Windows CI only runs Python 3.12, and Windows is
the shipping platform and the hardest teardown case. Do not claim
CI verified Windows 3.14. The reviewer runs the Dell check after
merge.
