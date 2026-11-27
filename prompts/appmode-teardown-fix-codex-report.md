# App mode teardown verification

The branch already contained the regression test in `2b578d2` and the fix in `af07783`. This run added only this report. It made no commits or pushes.

## Teardown order

After the controller stops, `IrisWindow.closeEvent` removes `view_host` from the layout and deletes it synchronously. It then deletes the `QWebView` only if the view still exists. Qt gives the container ownership of the embedded window. In the local probe, deleting the host also deleted the view. The validity check prevented a second deletion. [Qt for Python ownership documentation](https://doc.qt.io/qtforpython-6.8/PySide6/QtWidgets/QWidget.html#PySide6.QtWidgets.QWidget.createWindowContainer)

The host goes first so it cannot survive into QApplication shutdown with a stale reference to the view. Synchronous deletion completes before the close event accepts. The same order runs on Windows and Ubuntu. The Windows native WebView2 close still needs a hand check.

## Verification

- **Before the fix**. 1 failed. I ran the regression from an archive of the test-only commit `2b578d2` on Windows Python 3.14.10 with PySide6 6.12.0. The child exited 1 and reported that both objects were still alive after close. The test caught the lifetime defect before automatic shutdown.
- **After the fix**. 1 passed on the same interpreter and Qt version. `tests/test_appmode_teardown.py` uses a subprocess so a native crash stays outside the pytest process. The child closes a real offscreen Iris window with a stub controller. It checks both objects are gone and exits 0. The separate file keeps Qt state outside the in-process shell tests.
- **Order read-back**. An instrumented child confirmed that the controller stopped before deletion, the host had already left the layout, and the view was still valid when host deletion began. A second close made no further deletion calls. A deliberate reverse-order probe also exited 0 offscreen, so this regression does not distinguish the two orders on the local backend.
- **Full suite**. 455 passed and 51 subtests passed in 221.66 seconds. The first run reached 100% and hit a permissions error during pytest temp cleanup. A rerun with a fresh workspace temp directory and the pytest cache disabled exited 0.
- **Static checks**. `ruff check` passed. `ruff format --check` reported 223 files already formatted. Pyright reported 0 errors and 0 warnings on a clean archive of the tracked tree. The working-directory run found 9 errors in pre-existing scratch fixtures.
- **Demo and report scripts**. The visualizer and report read 6 seeded attempts across 3 demo traces, with 2 successes and 3 retries. The loopback proxy demo recorded 10 attempts. It allowed 6, blocked 1, and rate-limited 3. The cost was $0.00.
- **Secrets**. No credential patterns matched in the two teardown files or this report. This was a targeted local scan.

## Open verification

- The pull request workflow runs the subprocess on Ubuntu Python 3.14. I checked the workflow configuration. An Actions result for this branch was unavailable. The pull request Windows job runs Python 3.12. The full workflow also lists Windows Python 3.14 for pushes to main and manual runs.
- After merge, start Iris on the Dell with Windows Python 3.14, close the visible app window, and confirm that the process exits cleanly. This checks the shipping platform's native close path.
- Token usage was unavailable.
