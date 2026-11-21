"""Bench Part B, test 1b: the Pause row's dest_host in the setup window.

Test-first. This test is expected to FAIL on main cfe5204 (both
setup-window fixes merged). A failure is the proof that the Pause row
is written with an empty dest_host when Pause lands in the window.
Charles reviews this test first. Nothing is fixed until he does.

Problem in one sentence: a human presses Pause on a Run whose tunnel is
still connecting upstream, and the Pause row is journaled with an empty
dest_host, because the destination is only known from a registered live
tunnel or from an earlier journal row, and the window has neither.

Why it matters: the journal is the proof of what the human stopped. A
Pause row that names no destination cannot say where the paused Run was
going, which is the one thing the row is for. Part A already pinned this
rule for an open tunnel (test_pause_row_names_live_destination, the fix
in commit 54498fc). This test pins the same rule for the window.

How it is checked, deterministic, no sleeps and no luck:
  - the test holds the proxy's upstream connect, same harness as test one
  - it waits until the proxy is inside the window, presses Pause, and
    reads the journal at once, BEFORE releasing the connect
  - pause_run writes its row before it returns, so at that moment the
    pause row is the only row for the Run, and the proxy's own refusal
    row cannot exist yet. There is nothing to race and nothing to poll.

What must be true: every pause row for the Run names the destination the
Run was connecting to, in the same form Part A uses for an open tunnel
("127.0.0.1:<port>"), never an empty string.

This test does not check what the tunnel does after the Pause. Test one
covers that. It checks the journal row only. The row's endpoint column is
empty in the window for the same reason; this test pins dest_host only,
as asked, so the fix is free to choose how to fill endpoint.

Loopback only. Metadata only. No bodies, no keys, no real hostnames.
"""
import os
import socket
import socketserver
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import proxy as proxy_module

# How long the test waits for each step before it fails loudly.
STEP_TIMEOUT = 5
# How long the held upstream connect waits for release before it gives
# up. Longer than STEP_TIMEOUT so a slow machine never turns the gate
# into a false upstream failure.
HOLD_TIMEOUT = 10


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class PauseRowNamesDestinationInWindowTest(unittest.TestCase):
    """The Pause row names the destination when Pause lands in the window."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.echo = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _EchoHandler)
        cls.echo_port = cls.echo.server_address[1]
        threading.Thread(target=cls.echo.serve_forever, daemon=True).start()
        # High rate limit: rate limiting has its own tests, and a 429
        # here would mask the window this test needs to enter.
        cls.proxy = proxy_module.serve(
            "127.0.0.1", 0, cls.db_path, rate_limit=10000)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        cls.dest = f"127.0.0.1:{cls.echo_port}"

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.echo.shutdown()
        cls.proxy.server_close()
        cls.echo.server_close()
        cls.proxy.state.conn.close()
        cls.tmp.cleanup()

    def rows_for(self, trace_id):
        """All journal rows for one trace_id, oldest first. Read only."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT * FROM attempts WHERE trace_id = ? ORDER BY id",
                (trace_id,),
            ).fetchall()
        finally:
            conn.close()

    def test_pause_row_in_setup_window_names_the_destination(self):
        trace = "bench-setup-window-pause-dest-1"
        in_window = threading.Event()   # proxy is inside the upstream connect
        release = threading.Event()     # test lets the connect finish
        real_create_connection = socket.create_connection
        echo_port = self.echo_port

        def held_create_connection(address, *args, **kwargs):
            # Hold only the proxy's connect to the echo upstream, and
            # only the first one. The test client's own connect goes to
            # the proxy port, so it passes straight through.
            if address[1] == echo_port and not in_window.is_set():
                in_window.set()
                if not release.wait(timeout=HOLD_TIMEOUT):
                    raise OSError("test harness: upstream hold never released")
            return real_create_connection(address, *args, **kwargs)

        result = {}

        def client():
            try:
                sock = socket.create_connection(
                    ("127.0.0.1", self.proxy_port), timeout=HOLD_TIMEOUT)
                sock.settimeout(HOLD_TIMEOUT)
                sock.sendall(
                    (f"CONNECT {self.dest} HTTP/1.1\r\n"
                     f"Host: {self.dest}\r\n"
                     f"X-Trace-Id: {trace}\r\n\r\n").encode())
                result["sock"] = sock
                # Read the proxy's answer so the worker ends cleanly;
                # what the answer is does not matter to this test.
                sock.recv(65536)
            except Exception as exc:  # reported by the assertions below
                result["error"] = repr(exc)

        pause_rows = []
        with mock.patch.object(
                socket, "create_connection", held_create_connection):
            worker = threading.Thread(target=client, daemon=True)
            worker.start()
            try:
                self.assertTrue(
                    in_window.wait(timeout=STEP_TIMEOUT),
                    "harness: proxy never reached the upstream connect, "
                    "so the setup window was never entered")
                self.assertTrue(
                    self.proxy.state.pause_run(trace),
                    "pause_run refused the Run")
                # Read the journal NOW, while the connect is still held:
                # the pause row is the only row for this Run at this point.
                pause_rows = [r for r in self.rows_for(trace)
                              if r["outcome"] == "paused"]
            finally:
                release.set()
            worker.join(timeout=HOLD_TIMEOUT + STEP_TIMEOUT)

        try:
            self.assertFalse(worker.is_alive(), "harness: client thread hung")
            self.assertNotIn("error", result, result.get("error"))
            self.assertTrue(
                pause_rows, "pause_run returned but journaled no pause row")
            for row in pause_rows:
                self.assertEqual(
                    row["dest_host"], self.dest,
                    "Pause landed while the tunnel was still connecting "
                    "upstream, and the pause row does not name the "
                    "destination the Run was going to")
        finally:
            if "sock" in result:
                result["sock"].close()


if __name__ == "__main__":
    unittest.main()
