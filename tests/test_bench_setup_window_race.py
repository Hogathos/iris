"""Bench Part B, test one: Pause during the tunnel setup window.

Test-first. This test is expected to FAIL on main b1492f1. A failure is
the proof that the race is real, and a candidate explanation for the
CI run 77 redirect failure (same shape: a steer cut that finds nothing
to cut). Charles reviews this test first. Nothing is fixed until he does.

Problem in one sentence: a human presses Pause on a Run whose tunnel is
still connecting upstream, Pause finds no live tunnel to cut, and the
tunnel then opens and carries traffic for a Run the human already paused.

The window, from reading proxy.py do_CONNECT:
  1. the gates run: is_paused, override, cap, red gate, allowlist
  2. socket.create_connection to upstream runs (timeout up to 10 s)
  3. only then is the tunnel registered as live (_register_live)
A Pause between step 1 and step 3 sets the paused state, but the tunnel
is not in the live set yet, so there is nothing to cut, and there is no
check after step 3. Unverified until this test runs and fails.

How the window is held open, deterministically, no sleeps and no luck:
the test wraps socket.create_connection so that the proxy's upstream
connect, and only that one (matched by the echo port), stops at a gate
the test controls. The test waits until the proxy is inside the window,
presses Pause, then releases the gate.

What must be true afterwards, either outcome is correct:
  - the proxy refuses the tunnel with 503 (paused), or
  - the tunnel opens and is cut at once, so it carries no more bytes:
    EOF or error, never echo. A timeout means the tunnel is still alive,
    and that fails loudly.

Loopback only. Metadata only. No bodies, no keys, no real hostnames.
"""
import os
import socket
import socketserver
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


class PauseDuringSetupWindowTest(unittest.TestCase):
    """Pause that lands while the tunnel is still connecting upstream."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.echo = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _EchoHandler)
        cls.echo_port = cls.echo.server_address[1]
        threading.Thread(target=cls.echo.serve_forever, daemon=True).start()
        # High rate limit: rate limiting has its own tests, and a 429
        # here would mask the 503 or the live tunnel this test checks.
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

    def assert_tunnel_dead(self, sock):
        """A cut tunnel carries no more bytes: EOF or error, never echo.

        Same rule as the Pause race fix, runs 67 to 71. A graceful
        hangup reads b"" (EOF), an abrupt one raises OSError, and on
        Linux the first send into a just-shut socket can still succeed,
        so the receive side decides. A timeout means still alive.
        """
        sock.settimeout(STEP_TIMEOUT)
        try:
            sock.sendall(b"ping-after-pause")
        except socket.timeout:
            self.fail("tunnel send timed out: the tunnel is still alive")
        except OSError:
            return
        try:
            data = sock.recv(65536)
        except socket.timeout:
            self.fail("tunnel recv timed out: the tunnel is still alive")
        except OSError:
            return
        self.assertEqual(
            data, b"",
            "Pause landed while the tunnel was still connecting upstream, "
            "and the tunnel carries bytes anyway: nothing cut it")

    def test_pause_during_setup_window_leaves_tunnel_dead(self):
        trace = "bench-setup-window-pause-1"
        in_window = threading.Event()   # proxy is inside step 2
        release = threading.Event()     # test lets step 2 finish
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
                raw = b""
                while b"\r\n\r\n" not in raw:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
                result["sock"] = sock
                result["status"] = int(raw.split(b" ", 2)[1]) if raw else None
            except Exception as exc:  # reported by the assertions below
                result["error"] = repr(exc)

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
            finally:
                release.set()
            worker.join(timeout=HOLD_TIMEOUT + STEP_TIMEOUT)

        self.assertFalse(worker.is_alive(), "harness: client thread hung")
        self.assertNotIn("error", result, result.get("error"))
        sock = result["sock"]
        try:
            if result["status"] == 200:
                # Tunnel opened after the Pause: it must already be cut.
                self.assert_tunnel_dead(sock)
            else:
                # Refused instead: must be the paused refusal.
                self.assertEqual(
                    result["status"], 503,
                    "a tunnel refused after Pause must be 503 (paused)")
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
