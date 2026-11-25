"""Consumer app shell: one window on the loopback panel.

App mode is one window. The window shows the Iris dashboard, which is
the existing panel server, inside a native web view, not in a browser.
The proxy and the panel server start inside the app process on worker
threads. Verify, Pause, and Emergency stop are native buttons outside
the web view, so they work even if the web view fails. Closing the
window stops both servers. No orphan proxy.

The shell uses QtWebView, not WebEngine. QWebView has setUrl, not
load. It reports loadingChanged with QWebViewLoadingInfo. It has no
toHtml. On Windows it rides on WebView2, which the system provides.
The panel is plain server-rendered HTML forms, so this is enough.

Loopback only. Metadata only. No bodies, no keys. Plain words only.
The shell never sets QT_QPA_PLATFORM: a real window needs a real
display. Tests set offscreen themselves, before importing Qt.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import api as api_module
import proxy as proxy_module
from appmode import connect as connect_module
from appmode import restart as restart_module
from devpanel import panel as panel_module
from devpanel import pause_run as seam_pause_run
from devpanel import states as states_module
from devpanel import verify_run as seam_verify_run
from PySide6.QtCore import QUrl
from PySide6.QtWebView import QtWebView, QWebView
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenuBar, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QStatusBar,
    QVBoxLayout, QWidget)

# Default ports: the panel beside the proxy, both loopback only.
DEFAULT_PANEL_PORT = 8000
DEFAULT_PROXY_PORT = 8888

# How long start waits for the panel server to answer, in seconds.
# uvicorn needs about a second. Ten is patience, not slop.
PANEL_READY_TIMEOUT = 10.0

# How long stop waits for each server thread, in seconds. Closing Iris
# asks the watcher to stop and waits up to 3 seconds. A thread that
# will not stop is left, never chased past this: the process exits
# anyway, and nothing is left listening behind it on purpose.
STOP_JOIN_TIMEOUT = 3.0

# How long one beacon ping waits, in seconds.
BEACON_PING_TIMEOUT = 2.0

# Loading state: after this many seconds still loading, offer a retry.
LOADING_SLOW_SECONDS = 10.0

# The last result stays on the status bar this long, in seconds.
RESULT_HOLD_SECONDS = 10.0

# A Run with no live tunnel and nothing new for this long reads done.
IDLE_FINISH_SECONDS = 60.0

# First-run persistence: written beside the journal once the human
# picks demo or skip. Closing the window before that shows the welcome
# screen again next launch.
FIRST_RUN_FLAG = "first-run-done"

# Where Check for updates opens.
DOWNLOADS_URL = "https://github.com/Hogathos/iris/releases"

VERIFY_TOOLTIP = panel_module.VERIFY_TOOLTIP
PAUSE_TOOLTIP = panel_module.PAUSE_TOOLTIP
RESUME_TOOLTIP = panel_module.RESUME_TOOLTIP
EMERGENCY_TOOLTIP = panel_module.EMERGENCY_TOOLTIP
MORE_TOOLTIP = panel_module.MORE_TOOLTIP


def short_run_name(trace_id: str) -> str:
    """A Run is labelled Run plus the first 8 of its trace id."""
    return panel_module._short(trace_id)


def panel_url(panel_port: int, tier: str = "simple") -> str:
    """The view URL: the loopback panel, at one tier."""
    return f"http://127.0.0.1:{panel_port}/?tier={tier}"


def default_journal_name_for(date_text: str) -> str:
    """The journal file name for one day: iris-journal-{date}."""
    return f"iris-journal-{date_text}"


def _default_db_path() -> str:
    """Journal path: beside the API module, unless frozen.

    Frozen (the packaged exe): the bundle is versioned output,
    so the journal lives in per-user data outside it. The
    installer creates the folder; the app creates it too, so a
    first run without the installer still works.
    """
    if getattr(sys, "frozen", False):
        data_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "Iris Guardian")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "attempts.db")
    return api_module.DB_PATH


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
    Starting installs the per-launch token and the consumer-build
    flag on the panel server; stopping clears both.
    """

    def __init__(self, db_path: str | None = None,
                 panel_port: int | None = None,
                 proxy_port: int | None = None,
                 instance_name: str = "iris-app-mode"):
        self.db_path = db_path or _default_db_path()
        # Port 0 is a real port: only None means the default.
        self.panel_port: int | None = (
            panel_port if panel_port is not None else DEFAULT_PANEL_PORT)
        self.proxy_port = (
            proxy_port if proxy_port is not None else DEFAULT_PROXY_PORT)
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
        except FileExistsError:
            self._fail(
                f"Iris is already running on this machine "
                f"({self.instance_name}).",
                already_running=True)
            return False
        except (FileNotFoundError, PermissionError):
            folder = os.path.dirname(
                os.path.abspath(self.db_path)) or "."
            self._fail(
                f"Iris could not start: the folder {folder} is not "
                f"writable, so Iris cannot claim this machine. Make "
                f"the folder writable, then start Iris again.")
            return False
        except OSError as exc:
            self._fail(
                f"Iris could not start: {exc}.")
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
        panel_module.install_identity(panel_port)
        panel_module.set_consumer_build(True)
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
        panel_module.clear_identity()
        panel_module.set_consumer_build(False)
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
    Verify go through the dev panel seams, Watching and the demo go
    through the state, the stop through the proxy's own red button.
    Same hand, same journal, same words.
    """

    def __init__(self, state_getter):
        self._state_getter = state_getter

    def pause(self, trace_id):
        """Pause one Run. False when there is no live proxy."""
        state = self._state_getter()
        if state is None:
            return False
        return seam_pause_run(state, trace_id, reason="human pause")

    def verify(self, trace_id, read_back):
        """Verify one Run with the typed words. False when refused."""
        state = self._state_getter()
        if state is None:
            return False
        return panel_module.devpanel_verify(state, trace_id, read_back)

    def red_button(self):
        """Stop every Run at once. Nothing when there is no proxy."""
        state = self._state_getter()
        if state is None:
            return
        state.red_button()

    def lift(self):
        """Let agents work again. Nothing when there is no proxy."""
        state = self._state_getter()
        if state is None:
            return False
        return state.lift_red_button()

    def set_watching(self, on):
        """Turn watching off or on. False when there is no proxy."""
        state = self._state_getter()
        if state is None:
            return False
        return state.set_watching(on)

    def redirect(self, trace_id, dest):
        """Send one Run somewhere else. False when refused."""
        state = self._state_getter()
        if state is None:
            return False
        return state.redirect_run(trace_id, dest)

    def allow_place(self, dest):
        """Allow one place in one click. False when refused."""
        state = self._state_getter()
        if state is None:
            return False
        return state.allow_place(dest)

    def unallow_place(self, dest):
        """Stop allowing one place. The Undo behind the strip."""
        state = self._state_getter()
        if state is None:
            return False
        return state.unallow_place(dest)

    def run_demo(self):
        """Run the demo agent now, on this computer. Its trace id."""
        from appmode import demo as demo_module

        state = self._state_getter()
        if state is None:
            return None
        folder = os.path.dirname(os.path.abspath(
            self._journal_path()))
        return demo_module.run_demo(
            state, os.path.join(folder, "demo-notebook.json"))

    def _journal_path(self):
        import api as api_module

        return api_module.DB_PATH


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


class _DemoWorker:
    """The demo agent on a worker thread. Stop joins it."""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()

    def start(self, func):
        """Run func on a daemon thread. One run at a time."""
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._guarded, args=(func,), daemon=True,
            name="iris-demo")
        self._thread.start()

    def _guarded(self, func):
        try:
            if not self._stop.is_set():
                func()
        except Exception:
            pass

    def stop(self):
        """Ask the demo to stop. The current claim finishes first."""
        self._stop.set()

    def join(self, timeout=None):
        """Wait for the demo thread, then forget it."""
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)


class _LoadingPage(QWidget):
    """The loading state page: starting words, a bar, a retry button.

    The builder fills these in after construction, so they are
    declared here and assigned there. Plain QWidget does not know
    them, which the type check refused.
    """

    _label: QLabel
    _bar: QProgressBar
    _retry: QPushButton


class _VerifyDialog(QDialog):
    """The Verify dialog: words in, stamp out. Never the web view."""

    _field: QLineEdit


class _LimitsDialog(QDialog):
    """The Limits dialog: spending, allowed, blocked, save."""

    _field: QLineEdit
    _no_limit: QCheckBox


class _SettingsDialog(QDialog):
    """The Settings dialog: journal, updates, spot, keep."""

    _keep: QComboBox


class IrisWindow(QWidget):
    """One window on the loopback panel.

    The dashboard sits in the web view. Verify, Pause, and Emergency
    stop are native buttons outside the view, so they work even when
    the view fails. The panel has no copies of them. The tier switch
    is the More detail switch: Simple hides the diagnostic columns
    and tabs, More detail on shows them all. Closing the window stops
    the controller and the demo worker: no orphan watcher.
    """

    def __init__(self, controller, seams=None, ask_words=None,
                 ask_confirm=None, pick_agent=None, show_message=None,
                 ask_restart=None, connect_fn=None, restart_fn=None,
                 parent=None):
        super().__init__(parent)
        self._controller = controller
        if seams is None:
            seams = _LiveSeams(lambda: controller.proxy_state)
        self._seams = seams
        self._ask_words = ask_words or self._ask_words_dialog
        self._ask_confirm = ask_confirm or self._ask_confirm_dialog
        self._pick_agent = pick_agent or self._pick_agent_dialog
        self._show_message = show_message or self._show_message_dialog
        self._ask_restart = ask_restart or self._ask_restart_dialog
        self._connect_fn = connect_fn or connect_module.connect_agent
        self._restart_fn = (restart_fn
                            or (lambda agent_id:
                                restart_module.restart_agent(agent_id)))
        self.setWindowTitle("Iris")
        self.resize(1100, 720)
        self.setMinimumSize(860, 560)
        self._advanced = False
        self._verifying = None
        self._last_result = None
        self._dismissed = set()
        self._allowed_here = set()
        self._port_error = False
        self._demo_worker = _DemoWorker()
        self._menu_bar = QMenuBar()
        self._build_menus()
        QtWebView.initialize()
        self.view = QWebView()
        self.view.setUrl(QUrl(controller.panel_url("simple")))
        self.watching_check = QCheckBox("Watching: On")
        self.watching_check.setChecked(True)
        self.watching_check.setToolTip(panel_module.WATCHING_ON_TOOLTIP)
        self.verify_button = QPushButton("Verify")
        self.verify_button.setToolTip(VERIFY_TOOLTIP)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setToolTip(PAUSE_TOOLTIP)
        self.send_button = QPushButton("Send somewhere else")
        self.send_button.setVisible(False)
        self.tier_switch = QCheckBox("More detail")
        self.tier_switch.setToolTip(MORE_TOOLTIP)
        self.connect_button = QPushButton("Connect")
        self.stop_button = QPushButton("Emergency stop")
        self.stop_button.setToolTip(EMERGENCY_TOOLTIP)
        self.stop_button.setStyleSheet(
            "background-color: red; color: white;")
        top = QHBoxLayout()
        top.addWidget(self.watching_check)
        top.addWidget(self.verify_button)
        top.addWidget(self.pause_button)
        top.addWidget(self.send_button)
        top.addWidget(self.tier_switch)
        top.addWidget(self.connect_button)
        top.addStretch(1)
        top.addWidget(self.stop_button)
        self._strip = QWidget()
        self._strip_layout = QHBoxLayout(self._strip)
        self._strip_label = QLabel("")
        self._strip_label.setWordWrap(True)
        self._strip_layout.addWidget(self._strip_label)
        self._strip.setVisible(False)
        # QWebView is a QWindow, not a QWidget. The container
        # hosts its native surface inside the widget layout, below
        # the top bar, so the dashboard paints inside the window.
        # Same engine, same view object the tests drive.
        self.view_host = QWidget.createWindowContainer(self.view, self)
        # The hosted window brings no size of its own: without an
        # expanding policy the layout collapses it to zero height and
        # the window shows only the button bar.
        self.view_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.view_host.setMinimumSize(640, 480)
        self._web_page = self.view_host
        self._loading_page = self._build_loading_page()
        self._first_run_page, self._demo_button, self._skip_link = \
            self._build_first_run_page()
        self._status_bar = QStatusBar()
        self._status_text = QLabel("")
        self._status_cost = QLabel("")
        self._status_cost.setToolTip(panel_module.COST_TOOLTIP)
        self._status_bar.addWidget(self._status_text, 1)
        self._status_bar.addPermanentWidget(self._status_cost)
        layout = QVBoxLayout(self)
        layout.setMenuBar(self._menu_bar)
        layout.addLayout(top)
        layout.addWidget(self._strip)
        layout.addWidget(self.view_host)
        layout.addWidget(self._loading_page)
        layout.addWidget(self._first_run_page)
        layout.addWidget(self._status_bar)
        self._loading_page.setVisible(False)
        self._first_run_page.setVisible(False)
        self.verify_button.clicked.connect(self._on_verify)
        self.pause_button.clicked.connect(self._on_pause)
        self.stop_button.clicked.connect(self._on_stop)
        self.connect_button.clicked.connect(self._on_connect)
        self.send_button.clicked.connect(self._on_send)
        self.tier_switch.toggled.connect(self._on_tier)
        self.watching_check.toggled.connect(self._on_watching)
        self.view.urlChanged.connect(lambda _url: self._refresh_all())
        from PySide6.QtGui import QShortcut, QKeySequence
        self._estop_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+E"), self)
        self._estop_shortcut.activated.connect(self._on_stop)
        self._poll = None
        try:
            from PySide6.QtCore import QTimer
            self._poll = QTimer(self)
            self._poll.setInterval(1000)
            self._poll.timeout.connect(self._refresh_all)
        except Exception:
            self._poll = None
        self._choose_initial_page()

    def menuBar(self):
        """The native menu bar: Iris and Help."""
        return self._menu_bar

    def statusBar(self):
        """The native status bar: one sentence, cost on the right."""
        return self._status_bar

    def _build_menus(self):
        """The native menus: Iris and Help."""
        self._menu_bar.clear()
        iris = self._menu_bar.addMenu("Iris")
        run_demo = iris.addAction("Run the demo")
        run_demo.triggered.connect(self._choose_demo)
        copy_address = iris.addAction("Copy the address for your agent")
        copy_address.triggered.connect(self._on_copy_address)
        save_journal = iris.addAction("Save a copy of the journal…")
        save_journal.triggered.connect(self._on_save_journal)
        iris.addSeparator()
        limits = iris.addAction("Limits…")
        limits.triggered.connect(self._on_limits)
        settings = iris.addAction("Settings…")
        settings.triggered.connect(self._on_settings)
        if self._advanced:
            developer = iris.addAction("Developer page")
            developer.triggered.connect(self._on_developer_page)
        iris.addSeparator()
        close = iris.addAction("Close Iris")
        close.triggered.connect(self.close)
        help_menu = self._menu_bar.addMenu("Help")
        verdicts = help_menu.addAction("What do the verdicts mean?")
        verdicts.triggered.connect(self._on_verdicts)
        updates = help_menu.addAction("Check for updates")
        updates.triggered.connect(self._on_updates)
        about = help_menu.addAction("About Iris")
        about.triggered.connect(self._on_about)

    def _iris_menu_names(self, advanced=False):
        """The Iris menu action texts, with or without More detail."""
        names = ["Run the demo", "Copy the address for your agent",
                 "Save a copy of the journal…", "Limits…", "Settings…"]
        if advanced:
            names.append("Developer page")
        names.append("Close Iris")
        return names

    def _build_loading_page(self):
        """The loading state: Starting Iris, with a slow path."""
        page = _LoadingPage()
        layout = QVBoxLayout(page)
        label = QLabel(panel_module.LOADING_STARTING)
        bar = QProgressBar()
        bar.setRange(0, 0)
        retry = QPushButton(panel_module.LOADING_RETRY)
        retry.setVisible(False)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addWidget(bar)
        layout.addWidget(retry)
        layout.addStretch(2)
        retry.clicked.connect(
            lambda: self.view.setUrl(QUrl(self._controller.panel_url(
                "advanced" if self._advanced else "simple"))))
        page._label = label
        page._bar = bar
        page._retry = retry
        return page

    def _build_first_run_page(self):
        """The first-run screen: four lines, a button, a link."""
        from PySide6.QtCore import Qt

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(24)
        inner = QVBoxLayout()
        inner.setSpacing(24)
        labels = []
        for words in (panel_module.FIRST_RUN_ONE,
                      panel_module.FIRST_RUN_TWO,
                      panel_module.FIRST_RUN_THREE,
                      panel_module.FIRST_RUN_FOUR):
            label = QLabel(words)
            label.setWordWrap(True)
            inner.addWidget(label)
            labels.append(label)
        demo_button = QPushButton(panel_module.FIRST_RUN_DEMO)
        demo_line = QLabel(panel_module.FIRST_RUN_DEMO_LINE)
        row = QHBoxLayout()
        row.addWidget(demo_button)
        row.addWidget(demo_line)
        row.addStretch(1)
        inner.addLayout(row)
        skip_link = QPushButton(panel_module.FIRST_RUN_SKIP)
        skip_link.setFlat(True)
        skip_link.setStyleSheet("text-align: left; color: palette(link);"
                                " text-decoration: underline;")
        inner.addWidget(skip_link, alignment=Qt.AlignmentFlag.AlignLeft)
        inner.addStretch(1)
        wrap = QHBoxLayout()
        wrap.addLayout(inner, 1)
        wrap.addStretch(1)
        layout.addLayout(wrap)
        demo_button.clicked.connect(self._choose_demo)
        skip_link.clicked.connect(self._choose_skip)
        page.setMaximumWidth(640)
        return page, demo_button, skip_link

    def _journal_folder(self):
        """Where everything Iris saves is: beside the journal."""
        db_path = getattr(self._controller, "db_path", None)
        if not db_path:
            return None
        return os.path.dirname(os.path.abspath(db_path))

    def _first_run_flag(self):
        """The flag file: written once the human picks a path."""
        folder = self._journal_folder()
        if folder is None:
            return None
        return os.path.join(folder, FIRST_RUN_FLAG)

    def _is_first_run(self):
        """True on the first launch: an empty journal, no flag."""
        flag = self._first_run_flag()
        if flag is None or os.path.exists(flag):
            return False
        try:
            import sqlite3

            db_path = getattr(self._controller, "db_path", None)
            if not db_path or not os.path.exists(db_path):
                return True
            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM attempts").fetchone()[0]
            finally:
                conn.close()
            return count == 0
        except Exception:
            return True

    def _choose_initial_page(self):
        """Loading first, then the welcome screen or the dashboard."""
        if self._is_first_run():
            self._show_first_run()
        else:
            self._show_loading()
            if self._poll is not None:
                self._poll.start()

    def _show_loading(self):
        """The loading state fills the window while the panel starts."""
        self._loading_page._label.setText(panel_module.LOADING_STARTING)
        self._loading_page._retry.setVisible(False)
        self.view_host.setVisible(False)
        self._first_run_page.setVisible(False)
        self._loading_page.setVisible(True)
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(int(LOADING_SLOW_SECONDS * 1000),
                              self._on_loading_slow)
        except Exception:
            pass

    def _on_loading_slow(self):
        """After 10 seconds still loading: offer Try again."""
        if (self._loading_page.isVisible()
                and not self._port_error):
            self._loading_page._label.setText(panel_module.LOADING_SLOW)
            self._loading_page._retry.setVisible(True)

    def _show_first_run(self):
        """One screen. No account. No tour. No steps."""
        self.view_host.setVisible(False)
        self._loading_page.setVisible(False)
        self._first_run_page.setVisible(True)
        if self._poll is not None:
            self._poll.stop()

    def _show_dashboard(self):
        """The dashboard: the panel in the web view."""
        self._first_run_page.setVisible(False)
        self._loading_page.setVisible(False)
        self.view_host.setVisible(True)
        if self._poll is not None and not self._poll.isActive():
            self._poll.start()
        self._refresh_all()

    def _write_first_run_flag(self):
        """Remember the human got past the welcome screen."""
        flag = self._first_run_flag()
        if flag is None:
            return
        try:
            with open(flag, "w", encoding="utf-8") as handle:
                handle.write("seen\n")
        except OSError:
            pass

    def _choose_demo(self):
        """Run the demo was pressed: dashboard now, demo on it."""
        self._write_first_run_flag()
        self._show_dashboard()
        self._start_demo()

    def _choose_skip(self):
        """Skip was pressed: the dashboard with its empty state."""
        self._write_first_run_flag()
        self._show_dashboard()

    def _start_demo(self):
        """Start the demo agent on a worker thread. Costs $0.00."""
        func = getattr(self._seams, "run_demo", None)
        if func is None:
            state = self._controller.proxy_state
            if state is None:
                return
            folder = self._journal_folder()
            if folder is None:
                return
            notebook = os.path.join(folder, "demo-notebook.json")
            func = lambda: self._run_demo(state, notebook)
        self._demo_worker.start(func)

    def _run_demo(self, state, notebook):
        """The demo body: real notes, a real read-back, no staging."""
        from appmode import demo as demo_module

        try:
            trace_id = demo_module.run_demo(state, notebook)
        except Exception:
            return
        self._note_result(trace_id[:8], "Incorrect")

    def _current_run(self):
        """The Run the view shows, or None on the index page."""
        try:
            return _run_from_url(self.view.url().toString())
        except Exception:
            return None

    def _runs_now(self):
        """Fresh run summaries, or empty when the journal is quiet."""
        try:
            return panel_module._collect_runs()
        except Exception:
            return []

    def _run_row(self, trace_id):
        """The summary for one Run, or None when it is gone."""
        for run in self._runs_now():
            if run["trace_id"] == trace_id:
                return run
        return None

    def _maybe_autoselect(self):
        """With one running Run and none selected, select it."""
        if self._current_run() is not None:
            return
        try:
            running = [run for run in self._runs_now()
                       if run["state"] in ("FLOWING", "WARNED",
                                           "REDIRECTED")]
        except Exception:
            return
        if len(running) == 1:
            self.view.setUrl(QUrl(
                self._controller.panel_url(
                    "advanced" if self._advanced else "simple"
                ).replace("/?", f"/runs/{running[0]['trace_id']}?")))

    def _refresh_all(self):
        """One poll: selection, buttons, strip, and status."""
        try:
            self._maybe_autoselect()
            self._refresh_buttons()
            self._render_strip()
            self._refresh_status()
        except Exception:
            pass

    def _refresh_buttons(self):
        """Verify and Pause need a selected Run. The rest stay live."""
        trace_id = self._current_run()
        if trace_id is None:
            self.verify_button.setEnabled(False)
            self.verify_button.setToolTip(
                panel_module.PICK_FIRST_APP_TOOLTIP)
            self.pause_button.setEnabled(False)
            self.pause_button.setText("Pause")
            self.pause_button.setToolTip(
                panel_module.PICK_FIRST_APP_TOOLTIP)
            self.send_button.setVisible(self._advanced)
            self.send_button.setEnabled(False)
            return
        run = self._run_row(trace_id)
        state = run["state"] if run is not None else "FLOWING"
        self.verify_button.setEnabled(True)
        self.verify_button.setToolTip(VERIFY_TOOLTIP)
        self.pause_button.setEnabled(state not in ("STOPPED", "BLOCKED"))
        if state == "PAUSED":
            self.pause_button.setText("Resume")
            self.pause_button.setToolTip(RESUME_TOOLTIP)
        else:
            self.pause_button.setText("Pause")
            self.pause_button.setToolTip(PAUSE_TOOLTIP)
            if state in ("STOPPED", "BLOCKED"):
                self.pause_button.setToolTip("This Run is over.")
        over = state in ("STOPPED", "BLOCKED")
        self.send_button.setVisible(self._advanced)
        self.send_button.setEnabled(not over)
        watching = True
        proxy_state = self._controller.proxy_state
        if proxy_state is not None:
            try:
                watching = bool(proxy_state.watching)
            except Exception:
                watching = True
        self.watching_check.setChecked(watching)
        self.watching_check.setText(
            f"Watching: {'On' if watching else 'Off'}")
        self.stop_button.setEnabled(
            not self._emergency_on())

    def _emergency_on(self):
        """True when Emergency stop holds every call right now."""
        proxy_state = self._controller.proxy_state
        if proxy_state is None:
            return False
        try:
            return bool(proxy_state._stopped)
        except Exception:
            return False

    def _note_result(self, prefix, verdict):
        """Hold the last result on the status bar for 10 seconds."""
        self._last_result = (prefix, verdict, time.time())

    def _status_sentence(self):
        """The status bar sentence, left side."""
        if self._verifying is not None:
            return f"Checking Run {self._verifying}…"
        if self._emergency_on():
            return "Emergency stop is on."
        proxy_state = self._controller.proxy_state
        watching = True
        if proxy_state is not None:
            try:
                watching = bool(proxy_state.watching)
            except Exception:
                watching = True
        if not watching:
            return ("Watching is off. Agents that use Iris can't reach "
                    "anything.")
        if self._last_result is not None:
            prefix, verdict, when = self._last_result
            if time.time() - when < RESULT_HOLD_SECONDS:
                if verdict == "Incorrect":
                    return (f"Run {prefix} stopped. See what Iris "
                            "found below.")
                return (f"Run {prefix}: {verdict}. See what Iris "
                        "found below.")
        try:
            runs = self._runs_now()
        except Exception:
            runs = []
        count = sum(1 for run in runs
                    if run["state"] in ("FLOWING", "WARNED",
                                        "REDIRECTED"))
        if count == 1:
            return "Watching. 1 Run is running."
        if count > 1:
            return f"Watching. {count} Runs are running."
        return "Watching. No Runs are running."

    def _cost_text(self):
        """The status bar cost, right side."""
        try:
            return f"Cost: {panel_module._top_cost(self._runs_now())}"
        except Exception:
            return "Cost: $0.00"

    def _refresh_status(self):
        """Write the status bar: one sentence left, cost right."""
        try:
            self._status_text.setText(self._status_sentence())
            self._status_cost.setText(self._cost_text())
        except Exception:
            pass

    def _strip_for(self, emergency=None, blocked_place=None,
                   waiting=None, port=None):
        """The notice strip: one strip at a time, words and buttons.

        Returns (kind, words, buttons). Kind is one of emergency,
        waiting, blocked, allowed, port, or none. Waiting takes a
        trace prefix. Blocked takes a place, and reads allowed when
        the human already allowed it.
        """
        if emergency:
            return ("emergency", panel_module.STRIP_ESTOP,
                    ["Turn off Emergency stop…"])
        if waiting:
            return ("waiting",
                    panel_module.STRIP_WAITING.format(prefix=waiting),
                    ["Let it go on", "Keep it stopped"])
        if blocked_place is not None:
            if self._place_allowed(blocked_place):
                return ("allowed",
                        panel_module.STRIP_ALLOWED.format(
                            place=blocked_place),
                        ["Undo"])
            return ("blocked",
                    panel_module.STRIP_BLOCKED.format(
                        place=blocked_place),
                    [f"Allow {blocked_place}", "Dismiss"])
        if port:
            return ("port", panel_module.STRIP_PORT,
                    ["Try another spot", "Close Iris"])
        return ("none", "", [])

    def _place_allowed(self, place):
        """True when the human already allowed this place."""
        if place in self._allowed_here:
            return True
        proxy_state = self._controller.proxy_state
        if proxy_state is None:
            return False
        try:
            return bool(proxy_state.is_allowed_place(place))
        except Exception:
            return False

    def _strip_choice(self):
        """Which strip the window needs right now, if any."""
        if self._emergency_on():
            return self._strip_for(emergency=True)
        if self._port_error:
            return self._strip_for(port=True)
        override = getattr(self, "_strip_override", None)
        if override is not None:
            kind, place = override
            if kind == "allowed" and self._place_allowed(place):
                self._strip_place = place
                return self._strip_for(blocked_place=place)
            self._strip_override = None
        try:
            runs = self._runs_now()
        except Exception:
            runs = []
        waiting = [run for run in runs
                   if run["state"] in ("PAUSED", "REDIRECTED")]
        if waiting:
            return self._strip_for(
                waiting=waiting[0]["trace_id"][:8])
        proxy_state = self._controller.proxy_state
        if proxy_state is not None:
            try:
                limited = proxy_state.daily_limit is not None
                blocked = (proxy_state.recently_blocked(limit=20)
                           if limited else [])
            except Exception:
                blocked = []
            blocked = [place for place in blocked
                       if place not in self._dismissed
                       and not self._place_allowed(place)]
            if blocked:
                self._strip_place = blocked[0]
                return self._strip_for(blocked_place=blocked[0])
        return self._strip_for()

    def _render_strip(self):
        """Show the strip the window needs, or hide it."""
        for child in self._strip.findChildren(QPushButton):
            child.deleteLater()
        try:
            kind, words, buttons = self._strip_choice()
        except Exception:
            kind, words, buttons = ("none", "", [])
        if kind == "none":
            self._strip.setVisible(False)
            return
        self._strip_label.setText(words)
        for text in buttons:
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked=False, action=text, kind=kind:
                self._on_strip(action, kind))
            self._strip_layout.addWidget(button)
        self._strip.setVisible(True)

    def _on_strip(self, text, kind):
        """One strip button: act, then refresh at once."""
        if kind == "emergency":
            self._on_emergency_off()
        elif kind == "waiting":
            if text == "Let it go on":
                self._show_run_for_waiting()
            else:
                self._refresh_all()
        elif kind in ("blocked", "allowed"):
            if text == "Dismiss":
                self._dismiss_blocked()
            elif text == "Undo":
                self._undo_allowed()
            else:
                self._allow_place(text[len("Allow "):])
        elif kind == "port":
            if text == "Close Iris":
                self.close()
            else:
                self._on_try_another_spot()
        self._refresh_all()

    def _show_run_for_waiting(self):
        """Let it go on: open the waiting Run for its Verify."""
        runs = self._runs_now()
        waiting = [run for run in runs
                   if run["state"] in ("PAUSED", "REDIRECTED")]
        if not waiting:
            return
        self.view.setUrl(QUrl(
            self._controller.panel_url(
                "advanced" if self._advanced else "simple"
            ).replace("/?", f"/runs/{waiting[0]['trace_id']}?")))

    def _dismiss_blocked(self):
        """Dismiss hides the strip. The place stays blocked."""
        self._strip_override = None
        place = self._blocked_now()
        if place is not None:
            self._dismissed.add(place)

    def _blocked_now(self):
        """The place the strip offers right now, or None."""
        kind, words, _buttons = self._strip_choice()
        if kind != "blocked":
            return None
        proxy_state = self._controller.proxy_state
        if proxy_state is None:
            return None
        try:
            blocked = proxy_state.recently_blocked(limit=20)
        except Exception:
            return None
        blocked = [place for place in blocked
                   if place not in self._dismissed
                   and not self._place_allowed(place)]
        return blocked[0] if blocked else None

    def _undo_allowed(self):
        """Undo: stop allowing the place again."""
        self._strip_override = None
        place = getattr(self, "_strip_place", None)
        if place is not None:
            self._allow_place_undo(place)
            return
        for place in list(self._allowed_here):
            self._allow_place_undo(place)
            break

    def _allow_place(self, place, state=None):
        """Allow a place in one click. No typing, no confirmation."""
        target = state
        if target is None:
            target = self._controller.proxy_state
        if target is None:
            func = getattr(self._seams, "allow_place", None)
            if func is None:
                return False
            done = func(place)
        else:
            try:
                done = target.allow_place(place, reason="human allow")
            except TypeError:
                done = target.allow_place(place)
        if done:
            self._allowed_here.add(place)
            self._strip_override = ("allowed", place)
        return bool(done)

    def _allow_place_undo(self, place):
        """Undo one allow: the place blocks again under a limit."""
        self._allowed_here.discard(place)
        target = self._controller.proxy_state
        if target is not None:
            try:
                target.unallow_place(place, reason="human unallow")
                return
            except TypeError:
                try:
                    target.unallow_place(place)
                except Exception:
                    pass
                return
        func = getattr(self._seams, "unallow_place", None)
        if func is not None:
            try:
                func(place)
            except Exception:
                pass

    def _on_verify(self):
        """Verify the Run the view shows, with the typed words.

        Empty words never reach the seam: an empty read-back is
        refused, so the button keeps it local instead.
        """
        trace_id = self._current_run()
        if trace_id is None:
            return
        self._on_verify_for(trace_id)

    def _on_verify_for(self, trace_id):
        """Verify one Run: ask, stamp, and hold the result on screen."""
        words = self._ask_words()
        if words is None or not words.strip():
            return
        self._verifying = trace_id[:8]
        self._refresh_status()
        try:
            done = self._seams.verify(trace_id, words)
        finally:
            self._verifying = None
        if done:
            self._note_result(trace_id[:8], "Correct")
        self._refresh_all()

    def _on_pause(self):
        """Pause the Run the view shows. No Run, no call."""
        trace_id = self._current_run()
        if trace_id is None:
            return
        self._seams.pause(trace_id)
        self._refresh_all()

    def _on_stop(self):
        """Stop everything at once. One click, no confirmation."""
        self._seams.red_button()
        self._note_result("", "Incorrect")
        self._refresh_all()

    def _on_watching(self, on):
        """Watching off stops new calls; on lets them through again."""
        self.watching_check.setText(f"Watching: {'On' if on else 'Off'}")
        self.watching_check.setToolTip(
            panel_module.WATCHING_ON_TOOLTIP if on
            else panel_module.WATCHING_OFF_TOOLTIP)
        func = getattr(self._seams, "set_watching", None)
        if func is not None:
            func(bool(on))
        else:
            state = self._controller.proxy_state
            if state is not None:
                try:
                    state.set_watching(bool(on))
                except Exception:
                    pass
        self._refresh_all()

    def _on_send(self):
        """Send this Run somewhere else: pick, confirm, send."""
        trace_id = self._current_run()
        if trace_id is None:
            return
        run = self._run_row(trace_id)
        state = run["state"] if run is not None else "FLOWING"
        if state in ("STOPPED", "BLOCKED"):
            return
        dest, ok = QInputDialog.getText(
            self, "Send this Run somewhere else",
            "Pick where this Run's next calls should go.\nSend them to:")
        if not ok or not (dest or "").strip():
            return
        dest = dest.strip()
        prefix = trace_id[:8]
        box = QMessageBox(self)
        box.setWindowTitle("Send this Run somewhere else")
        box.setText(f"From now on, Run {prefix}'s calls go to {dest} "
                    f"instead. Do this?")
        cancel = box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole)
        send = box.addButton(
            "Send them there", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() != send:
            return
        func = getattr(self._seams, "redirect", None)
        if func is not None:
            func(trace_id, dest)
        else:
            proxy_state = self._controller.proxy_state
            if proxy_state is not None:
                try:
                    proxy_state.redirect_run(trace_id, dest)
                except Exception:
                    pass
        self._refresh_all()

    def _on_connect(self):
        """Connect one agent, show the result, offer the restart.

        The picker may return nothing: closing it without picking
        connects nothing, shows nothing, restarts nothing. A
        refusal shows in plain words and restarts nothing. "Save"
        and "Cancel" restart nothing.
        """
        agent_id = self._pick_agent()
        if agent_id is None:
            return
        proxy_url = (f"http://127.0.0.1:"
                     f"{self._controller.proxy_port}")
        try:
            result = self._connect_fn(
                agent_id, proxy_url, os.path.expanduser("~"))
        except connect_module.Refused as exc:
            self._show_message(str(exc))
            return
        except Exception as exc:
            self._show_message(
                f"Connect did not run: {exc}. "
                f"The agent is not connected.")
            return
        self._show_message(result)
        name = connect_module.AGENT_NAMES.get(agent_id, agent_id)
        choice = self._ask_restart(name)
        if choice == "restart":
            try:
                restart_result = self._restart_fn(agent_id)
            except Exception as exc:
                self._show_message(
                    f"Restart did not run: {exc}. The agent is "
                    f"still connected. Restart {name} by hand.")
            else:
                self._show_message(restart_result)
        elif choice == "save":
            self._show_message(
                f"Save your work in {name} first, then restart "
                f"{name} when ready. Iris cannot save it for you.")

    def _on_tier(self, advanced):
        """Flip the view to the other tier, same page."""
        self._advanced = bool(advanced)
        self._build_menus()
        tier = "advanced" if self._advanced else "simple"
        self.view.setUrl(
            QUrl(_with_tier(self.view.url().toString(), tier)))
        self._refresh_all()

    def _verify_dialog(self):
        """The Verify dialog: words in, stamp out. Never the web view."""
        dialog = _VerifyDialog(self)
        dialog.setWindowTitle("Verify")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("What was checked, in plain words:"))
        field = QLineEdit()
        layout.addWidget(field)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply)
        cancel = buttons.button(
            QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Cancel")
        apply_button = buttons.button(
            QDialogButtonBox.StandardButton.Apply)
        apply_button.setText("Verify")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog._field = field
        return dialog

    def _ask_words_dialog(self):
        """Ask what was checked, in a native dialog, never the view."""
        dialog = self._verify_dialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return dialog._field.text()

    def _emergency_off_dialog(self):
        """Turn-off dialog: Keep it on is the default."""
        box = QMessageBox(self)
        box.setWindowTitle("Let agents work again?")
        box.setText("Agents that use Iris will be able to make calls "
                    "again. Runs that were stopped stay stopped.")
        keep = box.addButton(
            "Keep it on", QMessageBox.ButtonRole.RejectRole)
        again = box.addButton(
            "Let agents work again", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep)
        return box

    def _on_emergency_off(self):
        """Turn off Emergency stop, but only after a human confirms."""
        box = self._emergency_off_dialog()
        box.exec()
        if box.clickedButton() is None:
            return
        if box.clickedButton().text() != "Let agents work again":
            return
        func = getattr(self._seams, "lift", None)
        if func is not None:
            func()
        else:
            proxy_state = self._controller.proxy_state
            if proxy_state is not None:
                try:
                    proxy_state.lift_red_button()
                except Exception:
                    pass
        self._refresh_all()

    def _ask_confirm_dialog(self):
        """Ask before stopping everything, in a native dialog."""
        return QMessageBox.question(
            self, "Emergency stop",
            "Stop every Run at once?") == QMessageBox.StandardButton.Yes

    def _on_copy_address(self):
        """Copy the address for the agent: this computer, proxy spot."""
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(
            f"http://127.0.0.1:{self._controller.proxy_port}")
        self._status_text.setText("Copied.")
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, self._refresh_status)
        except Exception:
            pass

    def default_journal_name(self, date_text):
        """The journal file name for one day: iris-journal-{date}."""
        return default_journal_name_for(date_text)

    def _save_note_text(self):
        """The note under the journal save dialog."""
        return panel_module.SAVE_NOTE

    def _on_save_journal(self):
        """Save a copy of the journal: runs, claims, no bodies."""
        import datetime

        folder = self._journal_folder()
        if folder is None:
            self._show_message("Iris could not save the journal: no "
                               "journal folder.")
            return
        day = datetime.datetime.now(
            datetime.timezone.utc).date().isoformat()
        path, _selected = QFileDialog.getSaveFileName(
            self, "Save a copy of the journal",
            os.path.join(folder, self.default_journal_name(day)))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self._journal_dict(), handle, indent=2)
        except OSError as exc:
            self._show_message(f"Iris could not save the journal: {exc}.")
            return
        self._status_text.setText(panel_module.SAVED_WORD)

    def _journal_dict(self):
        """The journal as data: runs, claims, costs. No bodies, no keys."""
        import datetime

        day = datetime.datetime.now(
            datetime.timezone.utc).date().isoformat()
        runs = []
        for run in self._runs_now():
            if panel_module._is_sample(run["rows"]):
                continue
            runs.append({
                "run": run["label"],
                "trace_id": run["trace_id"],
                "started": run["started"],
                "status": run["state_word"],
                "verdict": run["verdict"],
                "cost": run["cost"],
                "claims": [
                    {"claim": claim.get("claim", ""),
                     "verdict": claim.get("verdict", ""),
                     "looked_at": claim.get("looked_at", ""),
                     "found": claim.get("found", "")}
                    for claim in run["claims"]],
            })
        return {"journal": runs, "as_of": day}

    def _on_limits(self):
        """The Limits dialog: spending, places, blocks."""
        dialog = self._limits_dialog()
        dialog.exec()

    def _on_settings(self):
        """The Settings dialog: journal, updates, the spot, the keep."""
        dialog = self._settings_dialog()
        dialog.exec()

    def _on_verdicts(self):
        """What the verdicts mean: Correct, Incorrect, Unverified."""
        box = QMessageBox(self)
        box.setWindowTitle("What the verdicts mean")
        box.setText(self._verdict_help_text())
        box.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    def _verdict_help_text(self):
        """The verdict words, exactly as on screen."""
        return ("Correct. Iris looked, and what it found matches what "
                "the agent said.\n\n"
                "Incorrect. Iris looked, and what it found does not "
                "match what the agent said.\n\n"
                "Unverified. Iris could not look, or could not tell. "
                "Iris never guesses.")

    def _on_updates(self):
        """Check for updates opens the downloads page. Never itself."""
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(DOWNLOADS_URL))

    def _on_about(self):
        """About Iris: the name and the version, nothing else."""
        box = QMessageBox(self)
        box.setWindowTitle("About Iris")
        box.setText(f"Iris {panel_module._version()}")
        box.exec()

    def _on_developer_page(self):
        """Open the devpanel in the browser. More detail only."""
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(
            f"http://127.0.0.1:{self._controller.panel_port}/"))

    def _limits_dialog(self):
        """The Limits dialog: spending, allowed, blocked, save."""
        dialog = _LimitsDialog(self)
        dialog.setWindowTitle("Limits")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Spending."))
        layout.addWidget(QLabel(panel_module.LIMITS_WARN_LINE))
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Daily limit:"))
        state = self._controller.proxy_state
        try:
            daily = state.daily_limit if state is not None else None
        except Exception:
            daily = None
        field = QLineEdit("" if daily is None else f"{daily:.2f}")
        no_limit = QCheckBox("No limit")
        no_limit.setChecked(daily is None)
        limit_row.addWidget(field)
        limit_row.addWidget(no_limit)
        layout.addLayout(limit_row)
        layout.addWidget(QLabel(panel_module.LIMITS_COUNT_LINE))
        layout.addWidget(QLabel("Allowed places."))
        allowed_list = QComboBox()
        try:
            allowed = state.allowed_places() if state is not None else []
        except Exception:
            allowed = []
        allowed_list.addItems(allowed)
        layout.addWidget(allowed_list)
        allowed_row = QHBoxLayout()
        add_allowed = QPushButton("Add…")
        remove_allowed = QPushButton("Remove")
        allowed_row.addWidget(add_allowed)
        allowed_row.addWidget(remove_allowed)
        layout.addLayout(allowed_row)
        layout.addWidget(QLabel("Recently blocked."))
        recent_list = QComboBox()
        try:
            recent = state.recently_blocked(limit=20) \
                if state is not None else []
            recent = [place for place in recent
                      if not self._place_allowed(place)]
        except Exception:
            recent = []
        recent_list.addItems(recent)
        layout.addWidget(recent_list)
        allow_recent = QPushButton("Allow")
        layout.addWidget(allow_recent)
        layout.addWidget(QLabel("Blocked places."))
        blocked_list = QComboBox()
        try:
            blocked = sorted(state._overrides) \
                if state is not None else []
        except Exception:
            blocked = []
        blocked_list.addItems(blocked)
        layout.addWidget(blocked_list)
        blocked_row = QHBoxLayout()
        add_blocked = QPushButton("Add…")
        remove_blocked = QPushButton("Remove")
        blocked_row.addWidget(add_blocked)
        blocked_row.addWidget(remove_blocked)
        layout.addLayout(blocked_row)
        bottom = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save)
        layout.addWidget(bottom)
        dialog._field = field
        dialog._no_limit = no_limit
        add_allowed.clicked.connect(
            lambda: self._on_add_allowed(dialog, allowed_list))
        remove_allowed.clicked.connect(
            lambda: self._on_remove_allowed(
                dialog, allowed_list.currentText()))
        allow_recent.clicked.connect(
            lambda: self._on_allow_recent(
                dialog, recent_list.currentText(),
                allowed_list, recent_list))
        add_blocked.clicked.connect(
            lambda: self._on_add_blocked(dialog, blocked_list))
        remove_blocked.clicked.connect(
            lambda: self._on_remove_blocked(
                dialog, blocked_list.currentText()))
        bottom.accepted.connect(
            lambda: self._on_save_limits(dialog))
        bottom.rejected.connect(dialog.reject)
        return dialog

    def _on_add_allowed(self, dialog, allowed_list):
        """Add… is the fallback: type the address by hand."""
        dest, ok = QInputDialog.getText(
            dialog, "Allow a place", "Address of the place:")
        if not ok or not (dest or "").strip():
            return
        dest = dest.strip()
        box = QMessageBox(dialog)
        box.setWindowTitle("Allow a place")
        box.setText(f"Iris will let calls to {dest} through even "
                    f"though it can't work out what they cost. They "
                    f"will not count toward your limit. Allow it?")
        cancel = box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole)
        allow = box.addButton(
            "Allow it", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() != allow:
            return
        if self._allow_place(dest):
            allowed_list.addItem(dest)

    def _on_remove_allowed(self, dialog, dest):
        """Remove asks first: calls block again under a limit."""
        if not dest:
            return
        box = QMessageBox(dialog)
        box.setWindowTitle("Stop allowing a place")
        box.setText(f"Stop allowing {dest}? Calls to it will be "
                    f"blocked when a limit is set and Iris can't work "
                    f"out what the cost is.")
        cancel = box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole)
        stop = box.addButton(
            "Stop allowing it", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() != stop:
            return
        self._allow_place_undo(dest)

    def _on_allow_recent(self, dialog, dest, allowed_list, recent_list):
        """Recently blocked: one click, no typing, no confirmation."""
        if not dest:
            return
        if self._allow_place(dest):
            allowed_list.addItem(dest)
            recent_list.removeItem(recent_list.findText(dest))

    def _on_add_blocked(self, dialog, blocked_list):
        """Add… a blocked place: type it, confirm it, block it."""
        dest, ok = QInputDialog.getText(
            dialog, "Block a place", "Address of the place:")
        if not ok or not (dest or "").strip():
            return
        dest = dest.strip()
        box = QMessageBox(dialog)
        box.setWindowTitle("Block a place")
        box.setText(f"This will stop your agents reaching {dest}. "
                    f"Block it?")
        cancel = box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole)
        block = box.addButton(
            "Block it", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() != block:
            return
        state = self._controller.proxy_state
        if state is not None:
            try:
                if state.set_override(dest):
                    blocked_list.addItem(dest)
            except Exception:
                pass

    def _on_remove_blocked(self, dialog, dest):
        """Remove a block: let agents reach the place again."""
        if not dest:
            return
        box = QMessageBox(dialog)
        box.setWindowTitle("Unblock a place")
        box.setText(f"Let agents reach {dest} again?")
        cancel = box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole)
        let = box.addButton(
            "Let them", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() != let:
            return
        state = self._controller.proxy_state
        if state is not None:
            try:
                state.clear_override(dest)
            except Exception:
                pass

    def _on_save_limits(self, dialog):
        """Save limits asks first: agents are held to them at once."""
        box = QMessageBox(dialog)
        box.setWindowTitle("Save limits?")
        box.setText("Save these limits? Your agents are held to them "
                    "right away.")
        cancel = box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole)
        save = box.addButton(
            "Save limits", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() != save:
            return
        state = self._controller.proxy_state
        if state is None:
            dialog.accept()
            return
        try:
            if dialog._no_limit.isChecked():
                state.set_daily_limit(None)
            else:
                text = dialog._field.text().strip().lstrip("$")
                state.set_daily_limit(float(text))
        except (TypeError, ValueError):
            self._show_message("That limit was refused. Use a plain "
                               "dollar number above zero, or tick No "
                               "limit.")
            return
        dialog.accept()

    def _settings_dialog(self):
        """The Settings dialog: journal, updates, spot, keep."""
        dialog = _SettingsDialog(self)
        dialog.setWindowTitle("Settings")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Where Iris keeps its journal:"))
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(self._journal_folder()))
        open_folder = QPushButton("Open folder")
        folder_row.addWidget(open_folder)
        layout.addLayout(folder_row)
        layout.addWidget(QLabel(panel_module.SETTINGS_JOURNAL_LINE))
        layout.addWidget(QLabel("Updates:"))
        layout.addWidget(QLabel(panel_module.SETTINGS_UPDATES))
        open_folder.clicked.connect(
            lambda: self._open_folder(self._journal_folder()))
        if self._advanced:
            layout.addWidget(QLabel("The spot Iris listens on:"))
            spot_row = QHBoxLayout()
            spot_field = QLineEdit(str(self._controller.proxy_port))
            change = QPushButton("Change")
            spot_row.addWidget(spot_field)
            spot_row.addWidget(change)
            layout.addLayout(spot_row)
            layout.addWidget(QLabel(panel_module.SETTINGS_SPOT_LINE))
            layout.addWidget(QLabel("How long to keep the journal:"))
            keep = QComboBox()
            keep.addItems(["30 days", "1 year", "Until I delete them"])
            layout.addWidget(keep)
            delete = QPushButton("Delete the journal…")
            layout.addWidget(delete)
            change.clicked.connect(
                lambda: self._on_change_spot(spot_field.text()))
            delete.clicked.connect(self._on_delete_journal)
            dialog._keep = keep
            keep.currentIndexChanged.connect(
                lambda _index: self._on_keep_changed(dialog))
        close = QPushButton("Close")
        layout.addWidget(close)
        close.clicked.connect(dialog.accept)
        return dialog

    def _on_keep_changed(self, dialog):
        """Remember how long to keep the journal, at once."""
        keep = getattr(dialog, "_keep", None)
        if keep is None:
            return
        state = self._controller.proxy_state
        if state is None:
            return
        try:
            import db as db_module

            db_module.set_setting(
                state.conn, "journal_keep", keep.currentText())
        except Exception:
            pass

    def _open_folder(self, folder):
        """Open the journal folder in the file manager."""
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_change_spot(self, text):
        """Change the spot: the proxy restarts on the new number."""
        try:
            port = int(str(text).strip())
        except (TypeError, ValueError):
            self._show_message("That spot was refused. Use a plain "
                               "port number.")
            return
        if not 1 <= port <= 65535:
            self._show_message("That spot was refused. Use a port "
                               "number from 1 to 65535.")
            return
        controller = self._controller
        controller.stop()
        controller.proxy_port = port
        if not controller.start():
            self._port_error = True
            self._show_message(f"Iris could not start: "
                               f"{controller.start_error}")
            return
        self.view.setUrl(QUrl(controller.panel_url(
            "advanced" if self._advanced else "simple")))
        self._show_message("Iris is watching on a new address. Copy it "
                           "and give it to your agent.")

    def _on_try_another_spot(self):
        """The spot is taken: restart the proxy on a free one."""
        import socket as _socket

        probe = _socket.socket()
        try:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        except OSError:
            return
        finally:
            probe.close()
        self._port_error = False
        self._dismissed.clear()
        self._on_change_spot(str(port))
        self._show_message("Iris is watching on a new address. Copy it "
                           "and give it to your agent.")

    def _on_delete_journal(self):
        """Delete the whole journal asks first: nothing comes back."""
        box = QMessageBox(self)
        box.setWindowTitle("Delete the whole journal?")
        box.setText("This removes what every Run did and what Iris "
                    "found. You can't get it back. Iris can't check "
                    "past Runs after this.")
        keep = box.addButton(
            "Keep the journal", QMessageBox.ButtonRole.RejectRole)
        delete = box.addButton(
            "Delete the journal", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() != delete:
            return
        state = self._controller.proxy_state
        if state is not None:
            try:
                state.conn.execute("DELETE FROM attempts")
                state.conn.execute("DELETE FROM claims")
                state.conn.commit()
            except Exception:
                pass
        self._refresh_all()

    def _pick_agent_dialog(self):
        """Pick one known agent, in a native dialog, never the view.

        Returns the agent id, or None when the picker is closed
        without picking. Nothing is connected then.
        """
        from PySide6.QtWidgets import QInputDialog

        names = [connect_module.AGENT_NAMES[agent_id]
                 for agent_id in ("claude-code", "cursor", "continue")]
        picked, ok = QInputDialog.getItem(
            self, "Connect agent", "Pick an agent:",
            names, editable=False)
        if not ok:
            return None
        for agent_id, shown in connect_module.AGENT_NAMES.items():
            if shown == picked:
                return agent_id
        return None

    def _show_message_dialog(self, text):
        """Show plain words, in a native dialog, never the view.

        Shows the connect result or the refusal, never the whole
        config, so no secret leaks.
        """
        QMessageBox.information(self, "Iris", text)

    def _ask_restart_dialog(self, name):
        """Warn before restarting, in a native dialog, never the view.

        Exact words from the spec. "I'll save first" only closes
        the dialog: Iris cannot save the agent's work from
        outside, so the button never promises a save. Returns
        "restart", "save", or "cancel".
        """
        box = QMessageBox(self)
        box.setWindowTitle(f"Restart {name}?")
        box.setText(
            f"{name} must restart so it picks up the change. "
            f"Save your work in {name} first. "
            f"Iris cannot save your work for you.")
        save_button = box.addButton(
            "I'll save first", QMessageBox.ButtonRole.ActionRole)
        restart_button = box.addButton(
            "Restart", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == restart_button:
            return "restart"
        if clicked == save_button:
            return "save"
        return "cancel"

    def _running_now(self):
        """True when at least one Run is still going."""
        try:
            return any(run["state"] in ("FLOWING", "WARNED", "REDIRECTED")
                       for run in self._runs_now())
        except Exception:
            return False

    def closeEvent(self, event):
        """Ask when a Run is going, then stop everything by hand.

        The view is a QWindow in a QWindowContainer. Automatic
        destruction kills them in the wrong order on Python 3.14,
        and the app segfaults (upstream PYSIDE-3529). Explicit
        teardown here leaves automatic destruction nothing live.
        """
        if self._running_now():
            box = QMessageBox(self)
            box.setWindowTitle("A Run is still going.")
            box.setText("If you close Iris, your agent loses its "
                        "connection and the Run stops.")
            keep = box.addButton(
                "Keep Iris open", QMessageBox.ButtonRole.RejectRole)
            close = box.addButton(
                "Close Iris", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() != close:
                event.ignore()
                return
        from shiboken6 import delete as _delete_qobject
        from shiboken6 import isValid as _is_qobject_alive

        try:
            self._demo_worker.stop()
            self._demo_worker.join(timeout=STOP_JOIN_TIMEOUT)
        except Exception:
            pass
        self._controller.stop()
        # Container first, then the view. Never the reverse. The
        # crash is the container outliving its window
        # (QWindowContainer::toplevelAboutToBeDestroyed). The
        # container holds the only reference to the window surface.
        # Destroying the view first leaves the container over a dead
        # window, which is the same crash. Destroying the container
        # first unhooks the wrapper while the window still lives.
        # Then the view dies with no wrapper over it.
        # Synchronous delete on every platform. A posted delete
        # (deleteLater) leaves the native surface alive until the
        # loop pumps, and the loop never pumps during close on
        # Windows. The surface then dies in automatic destruction,
        # which is the crash again. Windows is the hardest case, so
        # it sets the rule for all platforms.
        layout = self.layout()
        host = getattr(self, "view_host", None)
        if host is not None and _is_qobject_alive(host):
            if layout is not None:
                layout.removeWidget(host)
            _delete_qobject(host)
        view = getattr(self, "view", None)
        if view is not None and _is_qobject_alive(view):
            _delete_qobject(view)
        event.accept()
