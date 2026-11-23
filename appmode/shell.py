"""Consumer app shell: one window on the loopback panel.

App mode is one window. The window shows the Iris dashboard, which is
the existing panel server, inside a native web view, not in a browser.
The proxy and the panel server start inside the app process on worker
threads. Verify, Pause, and Emergency stop are native buttons outside
the web view, so they work even if the web view fails. Closing the
window stops both servers. No orphan proxy.

The shell uses QtWebView, not WebEngine. QWebView has setUrl, not
load. It reports loadingChanged with QWebViewLoadingInfo, not
loadFinished. It has no toHtml. On Windows it rides on WebView2,
which the system provides. On Linux it rides on WebEngine. The panel
is plain server-rendered HTML forms, so this is enough.

Loopback only. Metadata only. No bodies, no keys. Plain words only.
The shell never sets QT_QPA_PLATFORM: a real window needs a real
display. Tests set offscreen themselves, before importing Qt.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import api as api_module
import proxy as proxy_module
from devpanel import panel as panel_module
from devpanel import pause_run as seam_pause_run
from devpanel import verify_run as seam_verify_run
from PySide6.QtCore import QUrl
from PySide6.QtWebView import QtWebView, QWebView
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QInputDialog, QMessageBox, QPushButton,
    QVBoxLayout, QWidget)

# Default ports: the panel beside the proxy, both loopback only.
DEFAULT_PANEL_PORT = 8000
DEFAULT_PROXY_PORT = 8888

# How long start waits for the panel server to answer, in seconds.
# uvicorn needs about a second. Ten is patience, not slop.
PANEL_READY_TIMEOUT = 10.0

# How long stop waits for each server thread, in seconds. A thread
# that will not stop is left, never chased past this: the process
# exits anyway.
STOP_JOIN_TIMEOUT = 10.0

# How long one beacon ping waits, in seconds.
BEACON_PING_TIMEOUT = 2.0


def panel_url(panel_port: int, tier: str = "simple") -> str:
    """The view URL: the loopback panel, at one tier."""
    return f"http://127.0.0.1:{panel_port}/?tier={tier}"


def _safe_name(text: str) -> str:
    """Instance name safe for a file name: letters stay, rest is _."""
    return "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_"
        for ch in text)


def _lock_path(db_path: str, instance_name: str) -> str:
    """Single-instance lock file, beside the journal.

    The proxy must write the journal, so the journal folder is
    writable by definition. No new folder, no new permission.
    """
    return os.path.join(
        os.path.dirname(os.path.abspath(db_path)),
        f"{os.path.basename(db_path)}.iris-{_safe_name(instance_name)}.lock")


def _ping_beacon(port: int) -> bool:
    """True when a beacon answers on this loopback port right now."""
    try:
        sock = socket.create_connection(
            ("127.0.0.1", port), timeout=BEACON_PING_TIMEOUT)
    except OSError:
        return False
    try:
        sock.close()
    except OSError:
        pass
    return True


def _port_free(port: int) -> bool:
    """True when nothing holds this loopback port right now."""
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _wait_for_port(port: int, timeout: float) -> bool:
    """True when something answers on this loopback port in time."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        probe = socket.socket()
        try:
            probe.settimeout(1.0)
            probe.connect(("127.0.0.1", port))
            return True
        except OSError:
            pass
        finally:
            probe.close()
        time.sleep(0.05)
    return False


class AppController:
    """Starts the proxy and the panel inside the app, stops both.

    One instance per name: a second controller with the same
    instance_name refuses in plain words and starts nothing. The
    first controller sees the attempt through its beacon socket.
    """

    def __init__(self, db_path: str | None = None,
                 panel_port: int | None = None,
                 proxy_port: int | None = None,
                 instance_name: str = "iris-app-mode"):
        self.db_path = db_path or api_module.DB_PATH
        self.panel_port: int | None = (
            panel_port if panel_port else DEFAULT_PANEL_PORT)
        self.proxy_port = proxy_port if proxy_port else DEFAULT_PROXY_PORT
        self.instance_name = instance_name
        self.already_running = False
        self.start_error = ""
        self.second_instance_seen = False
        self.proxy_state = None
        self._proxy = None
        self._proxy_thread = None
        self._panel_server = None
        self._panel_thread = None
        self._beacon = None
        self._beacon_thread = None
        self._beacon_stop = threading.Event()
        self._lock_path = _lock_path(self.db_path, self.instance_name)
        self._owns_lock = False
        self._stopped = False

    def panel_url(self, tier: str = "simple") -> str:
        """The view URL for this controller, at one tier."""
        if self.panel_port is None:
            raise RuntimeError(
                "Iris is already running on this machine: "
                "this controller started nothing.")
        return panel_url(self.panel_port, tier)

    def start(self) -> bool:
        """Start the beacon, the proxy, and the panel. True when all run.

        Plain words on failure, never a traceback: start_error says
        what happened and names the port. A failed start leaves
        nothing listening.
        """
        if self._proxy is not None:
            return True
        if self._beacon_taken():
            return False
        panel_port = self.panel_port
        if panel_port is None:
            return False
        # Fresh attempt: a past failure stopped everything, and the
        # stop flags must not leak into the new run.
        self._stopped = False
        self._beacon_stop.clear()
        try:
            self._open_beacon()
        except OSError:
            self._fail(
                f"Iris is already running on this machine "
                f"({self.instance_name}).",
                already_running=True)
            return False
        if not _port_free(panel_port):
            self._fail(
                f"Iris could not start: the panel port {panel_port} "
                f"is already in use. Stop the other program on it, "
                f"then start Iris again.")
            return False
        try:
            self._proxy = proxy_module.serve(
                "127.0.0.1", self.proxy_port, self.db_path)
        except OSError as exc:
            self._fail(
                f"Iris could not start: the proxy port {self.proxy_port} "
                f"is already in use. Stop the other program on it, "
                f"then start Iris again.")
            return False
        except Exception as exc:
            self._fail(
                f"Iris could not start the proxy: {exc}.")
            return False
        self.proxy_state = self._proxy.state
        self._proxy_thread = threading.Thread(
            target=self._proxy.serve_forever, daemon=True,
            name="iris-proxy")
        self._proxy_thread.start()
        api_module.DB_PATH = self.db_path
        panel_module.app.state.proxy_state = self._proxy.state
        try:
            self._start_panel(panel_port)
        except Exception as exc:
            self._fail(
                f"Iris could not start the panel on port "
                f"{panel_port}: {exc}.")
            return False
        if not _wait_for_port(panel_port, PANEL_READY_TIMEOUT):
            self._fail(
                f"Iris could not start: the panel on port "
                f"{panel_port} did not answer in time.")
            return False
        return True

    def stop(self) -> None:
        """Stop the panel and the proxy. Safe to call twice."""
        if self._stopped:
            return
        self._stopped = True
        self._beacon_stop.set()
        if self._panel_server is not None:
            self._panel_server.should_exit = True
        if self._proxy is not None:
            try:
                self._proxy.shutdown()
            except Exception:
                pass
            try:
                self._proxy.server_close()
            except Exception:
                pass
        for thread in (self._panel_thread, self._proxy_thread,
                       self._beacon_thread):
            if thread is not None:
                thread.join(timeout=STOP_JOIN_TIMEOUT)
        # The state holds the journal open: serving threads are joined,
        # so nothing writes now, and the handle must close before the
        # caller removes the folder. Otherwise the file stays locked.
        if self._proxy is not None:
            try:
                self._proxy.state.conn.close()
            except Exception:
                pass
        if self._beacon is not None:
            try:
                self._beacon.close()
            except Exception:
                pass
        if self._owns_lock:
            self._owns_lock = False
            try:
                if os.path.exists(self._lock_path):
                    os.remove(self._lock_path)
            except OSError:
                pass
        self._beacon = None
        self._proxy = None
        self._panel_server = None

    def _beacon_taken(self) -> bool:
        """True when a live first instance owns our name: refuse."""
        try:
            with open(self._lock_path, encoding="utf-8") as handle:
                claim = json.load(handle)
            port = int(claim.get("port", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return False
        if port and _ping_beacon(port):
            self._fail(
                f"Iris is already running on this machine "
                f"({self.instance_name}).",
                already_running=True)
            return True
        try:
            os.remove(self._lock_path)
        except OSError:
            pass
        return False

    def _open_beacon(self) -> None:
        """Claim our name: lock file plus a beacon socket, then listen.

        Exclusive create wins the race: the loser gets FileExistsError
        (an OSError), so two starters never both own one name.
        """
        self._beacon = socket.socket()
        self._beacon.bind(("127.0.0.1", 0))
        self._beacon.listen(5)
        self._beacon.settimeout(0.5)
        port = self._beacon.getsockname()[1]
        with open(self._lock_path, "x", encoding="utf-8") as handle:
            json.dump({"port": port}, handle)
        self._owns_lock = True
        self._beacon_thread = threading.Thread(
            target=self._watch_beacon, daemon=True,
            name="iris-beacon")
        self._beacon_thread.start()

    def _watch_beacon(self) -> None:
        """Mark each second start: an accept is a second instance."""
        beacon = self._beacon
        if beacon is None:
            return
        while not self._beacon_stop.is_set():
            try:
                conn, _addr = beacon.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.second_instance_seen = True
            try:
                conn.close()
            except OSError:
                pass

    def _start_panel(self, panel_port: int) -> None:
        """Run the panel server on its worker thread."""
        import uvicorn

        config = uvicorn.Config(
            panel_module.app, host="127.0.0.1", port=panel_port,
            log_level="warning")
        self._panel_server = uvicorn.Server(config)
        self._panel_thread = threading.Thread(
            target=self._panel_server.run, daemon=True,
            name="iris-panel")
        self._panel_thread.start()

    def _fail(self, words: str, already_running: bool = False) -> bool:
        """Record a plain-words failure, leave nothing listening."""
        self.start_error = words
        self.already_running = already_running
        if already_running:
            self.panel_port = None
        self.stop()
        return False


class _LiveSeams:
    """Human-only seams wired to the live proxy state.

    The window calls these, never the proxy directly: pause and
    Verify go through the dev panel seams, the stop through the
    proxy's own red button. Same hand, same journal, same words.
    """

    def __init__(self, state_getter):
        self._state_getter = state_getter

    def pause(self, trace_id: str):
        """Pause one Run. False when there is no live proxy."""
        state = self._state_getter()
        if state is None:
            return False
        return seam_pause_run(state, trace_id, reason="human pause")

    def verify(self, trace_id: str, read_back: str):
        """Verify one Run with the typed words. False when refused."""
        state = self._state_getter()
        if state is None:
            return False
        return seam_verify_run(state, trace_id, read_back=read_back)

    def red_button(self):
        """Stop every Run at once. Nothing when there is no proxy."""
        state = self._state_getter()
        if state is None:
            return
        state.red_button()


def _run_from_url(url: str):
    """The Run the view shows, or None on the index page."""
    path = urlsplit(url).path
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "runs" and parts[1]:
        return parts[1]
    return None


def _with_tier(url: str, tier: str) -> str:
    """The same view path at another tier."""
    split = urlsplit(url)
    path = split.path or "/"
    return urlunsplit(
        (split.scheme, split.netloc, path, f"tier={tier}", ""))


class IrisWindow(QWidget):
    """One window on the loopback panel.

    The dashboard sits in the web view. Verify, Pause, and Emergency
    stop are native buttons outside the view, so they work even when
    the view fails. The tier switch sits top-right: Simple hides the
    diagnostic columns and the Redirect control, Advanced shows all.
    Closing the window stops the controller: no orphan proxy.
    """

    def __init__(self, controller, seams=None, ask_words=None,
                 ask_confirm=None, parent=None):
        super().__init__(parent)
        self._controller = controller
        if seams is None:
            seams = _LiveSeams(lambda: controller.proxy_state)
        self._seams = seams
        self._ask_words = ask_words or self._ask_words_dialog
        self._ask_confirm = ask_confirm or self._ask_confirm_dialog
        self.setWindowTitle("Iris")
        QtWebView.initialize()
        self.view = QWebView()
        self.view.setUrl(QUrl(controller.panel_url("simple")))
        self.verify_button = QPushButton("Verify")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Emergency stop")
        self.tier_switch = QCheckBox("Advanced")
        top = QHBoxLayout()
        top.addWidget(self.verify_button)
        top.addWidget(self.pause_button)
        top.addWidget(self.stop_button)
        top.addStretch(1)
        top.addWidget(self.tier_switch)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        # QWebView is a QWindow, not a QWidget, and the offscreen
        # platform plugin has no setParent: hosting it in a widget
        # container segfaults there (and in CI), so the view stays a
        # bare member. It still loads the panel on setUrl, and the
        # native buttons drive it from outside.
        self.verify_button.clicked.connect(self._on_verify)
        self.pause_button.clicked.connect(self._on_pause)
        self.stop_button.clicked.connect(self._on_stop)
        self.tier_switch.toggled.connect(self._on_tier)

    def _current_run(self):
        """The Run the view shows, or None on the index page."""
        return _run_from_url(self.view.url().toString())

    def _on_pause(self):
        """Pause the Run the view shows. No Run, no call."""
        trace_id = self._current_run()
        if trace_id is None:
            return
        self._seams.pause(trace_id)

    def _on_verify(self):
        """Verify the Run the view shows, with the typed words.

        Empty words never reach the seam: an empty read-back is
        refused, so the button keeps it local instead.
        """
        trace_id = self._current_run()
        if trace_id is None:
            return
        words = self._ask_words()
        if words is None or not words.strip():
            return
        self._seams.verify(trace_id, words)

    def _on_stop(self):
        """Stop everything, but only after a human confirms."""
        if self._ask_confirm():
            self._seams.red_button()

    def _on_tier(self, advanced):
        """Flip the view to the other tier, same page."""
        tier = "advanced" if advanced else "simple"
        self.view.setUrl(
            QUrl(_with_tier(self.view.url().toString(), tier)))

    def _ask_words_dialog(self):
        """Ask what was checked, in a native dialog, never the view."""
        text, ok = QInputDialog.getText(
            self, "Verify", "What was checked, in plain words:")
        return text if ok else ""

    def _ask_confirm_dialog(self):
        """Ask before stopping everything, in a native dialog."""
        return QMessageBox.question(
            self, "Emergency stop",
            "Stop every Run at once?") == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        """Stop the proxy and the panel with the window. No orphan."""
        self._controller.stop()
        event.accept()
