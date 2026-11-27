"""App mode teardown test: closing the window must exit clean.

The embedded view (a QWindow) sits in a QWindowContainer. When the
window closes, both must die before QApplication does. On Python
3.14 the process segfaults in automatic destruction when the
container outlives its window (upstream PYSIDE-3529).

This test is subprocess-based. A segfault kills the test process,
so an in-process Qt test would kill the whole suite. The child
builds a real Iris window, closes it, checks the teardown, and
exits. The parent asserts exit code 0. Before the fix the child
exits 1 (both objects still alive) or segfaults on 3.14. After
the fix it exits 0.

New file, not an addition to test_appmode_shell.py: that module
owns one in-process QApplication with its own module teardown. A
subprocess test touches no in-process Qt state, so it keeps that
teardown undisturbed.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The child program. Offscreen first, before any Qt import. No
# show, no event pump: showing starts the renderer, and pumping
# crashes the offscreen backend on Windows. close() delivers the
# close event synchronously, so no pump is needed.
CHILD = """
import gc
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
for _key in ("NO_PROXY", "no_proxy"):
    _seen = [p.strip() for p in os.environ.get(_key, "").split(",")
             if p.strip()]
    for _host in ("127.0.0.1", "localhost"):
        if _host not in _seen:
            _seen.append(_host)
    os.environ[_key] = ",".join(_seen)

sys.path.insert(0, os.environ["IRIS_ROOT"])

import shiboken6
from PySide6.QtWidgets import QApplication

from appmode import shell as shell_module


class _StubController:
    def __init__(self):
        self.stopped = 0
        self.proxy_state = None

    def panel_url(self, tier="simple"):
        return "http://127.0.0.1:8000/?tier=simple"

    def stop(self):
        self.stopped += 1


app = QApplication.instance()
if app is None:
    app = QApplication([])

window = shell_module.IrisWindow(_StubController())
window.close()

problems = []
if shiboken6.isValid(window.view_host):
    problems.append("view_host still alive after close")
if shiboken6.isValid(window.view):
    problems.append("view still alive after close")
if problems:
    sys.stdout.write("TEARDOWN-FAIL: " + "; ".join(problems) + "\\n")
    sys.stdout.flush()
    sys.exit(1)

if sys.platform == "win32":
    shiboken6.delete(window)
else:
    window.deleteLater()
    QApplication.sendPostedEvents()
del window
del app
gc.collect()
sys.stdout.write("TEARDOWN-OK\\n")
sys.stdout.flush()
sys.exit(0)
"""


class TeardownTest(unittest.TestCase):
    """Close the window in a child process. Exit code must be 0."""

    def test_close_then_exit_is_clean(self):
        """The child closes a real Iris window and exits 0."""
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox"
        env["IRIS_ROOT"] = ROOT
        proc = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=ROOT, capture_output=True, text=True, timeout=180)
        tail = (proc.stdout + proc.stderr)[-2000:]
        self.assertEqual(proc.returncode, 0, msg=f"teardown child exited {proc.returncode}, out: {tail}")


if __name__ == "__main__":
    unittest.main()
