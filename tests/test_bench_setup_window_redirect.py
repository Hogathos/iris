"""Bench Part B, test two: Redirect during the tunnel setup window.

Test-first. This test is expected to FAIL on branch tip d3bd2a5 (the
Pause setup-window fix, test one green). A failure is the proof that
Redirect has the same race as Pause had, and it is the second
candidate explanation for the CI run 77 redirect failure.
Charles reviews this test first. Nothing is fixed until he does.

Problem in one sentence: a human redirects a Run away from destination A
while its tunnel is still connecting to A, Redirect finds no live tunnel
to cut, and the tunnel then opens to A and carries traffic for a Run that
was already moved off A.

The window is the same as in test one (proxy.py do_CONNECT): the gates
run, then socket.create_connection to upstream runs (up to 10 s), and
only then is the tunnel registered as live. A Redirect between the gates
and the registration sets the new route, but the tunnel is not in the
live set yet, so there is nothing to cut, and there is no redirect
check after registration. Unverified until this test runs and fails.

Redirect needs its own call, so this is the decision this test pins,
for Charles to review:

  A tunnel that connected to the OLD destination before the Redirect
  landed carries no bytes: it is refused with 503, or it opens and is
  cut at once. It is NOT re-routed in flight to the new destination.
  Why: the gates for the new destination (override, spend cap, rate
  limit) have not run for it, so an in-flight re-route would skip them.
  The client's retry takes the normal path, through every gate, to the
  new destination. Rejected: refusing a redirected Run for good. The
  Run is steered, not stuck, so the test finishes with a retry that
  must reach the new destination.

What the test proves, in order:
  1. the old destination (A) received zero bytes from the window tunnel
  2. the window tunnel is refused (503) or dead: EOF or error, never
     echo; a timeout means still alive and fails loudly
  3. a retry on the same Run goes to the new destination (C), and A
     still has zero bytes

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


class _CountingEchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream that counts the bytes it received.

    Test helper only. It counts bytes, never stores them.
    """

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            with self.server.count_lock:
                self.server.bytes_in += len(data)
            self.request.sendall(data)


def _start_counting_echo():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _CountingEchoHandler)
    server.bytes_in = 0
    server.count_lock = threading.Lock()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class RedirectDuringSetupWindowTest(unittest.TestCase):
    """Redirect that lands while the tunnel is still connecting upstream."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.old_dest = _start_counting_echo()  # A: where the Run was
        cls.new_dest = _start_counting_echo()  # C: where the human sends it
        cls.old_port = cls.old_dest.server_address[1]
        cls.new_port = cls.new_dest.server_address[1]
        # High rate limit: rate limiting has its own tests, and a 429
        # here would mask the 503 or the live tunnel this test checks.
        cls.proxy = proxy_module.serve("127.0.0.1", 0, cls.db_path, rate_limit=10000)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.old_dest.shutdown()
        cls.new_dest.shutdown()
        cls.proxy.server_close()
        cls.old_dest.server_close()
        cls.new_dest.server_close()
        cls.proxy.state.conn.close()
        cls.tmp.cleanup()

    def open_tunnel(self, trace):
        """CONNECT to the OLD destination for this Run. Returns (sock, status).

        The client always asks for A. Where the tunnel really goes is the
        proxy's call, and that is what this test checks.
        """
        dest = f"127.0.0.1:{self.old_port}"
        sock = socket.create_connection(("127.0.0.1", self.proxy_port), timeout=HOLD_TIMEOUT)
        sock.settimeout(HOLD_TIMEOUT)
        sock.sendall((f"CONNECT {dest} HTTP/1.1\r\nHost: {dest}\r\nX-Trace-Id: {trace}\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
        status = int(raw.split(b" ", 2)[1]) if raw else None
        return sock, status

    def assert_tunnel_dead(self, sock):
        """A cut tunnel carries no more bytes: EOF or error, never echo.

        Same rule as the Pause race fix, runs 67 to 71. A graceful
        hangup reads b"" (EOF), an abrupt one raises OSError, and on
        Linux the first send into a just-shut socket can still succeed,
        so the receive side decides. A timeout means still alive.
        """
        sock.settimeout(STEP_TIMEOUT)
        try:
            sock.sendall(b"ping-after-redirect")
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
            data,
            b"",
            "Redirect landed while the tunnel was still connecting to the "
            "old destination, and the tunnel carries bytes anyway: "
            "nothing cut it",
        )

    def test_redirect_during_setup_window_leaves_old_route_dead(self):
        trace = "bench-setup-window-redirect-1"
        in_window = threading.Event()  # proxy is inside the upstream connect
        release = threading.Event()  # test lets the connect finish
        real_create_connection = socket.create_connection
        old_port = self.old_port

        def held_create_connection(address, *args, **kwargs):
            # Hold only the proxy's connect to the OLD destination, and
            # only the first one. The test client's connect goes to the
            # proxy port, and the retry's connect goes to the new
            # destination port, so both pass straight through.
            if address[1] == old_port and not in_window.is_set():
                in_window.set()
                if not release.wait(timeout=HOLD_TIMEOUT):
                    raise OSError("test harness: upstream hold never released")
            return real_create_connection(address, *args, **kwargs)

        result = {}

        def client():
            try:
                result["sock"], result["status"] = self.open_tunnel(trace)
            except Exception as exc:  # reported by the assertions below
                result["error"] = repr(exc)

        with mock.patch.object(socket, "create_connection", held_create_connection):
            worker = threading.Thread(target=client, daemon=True)
            worker.start()
            try:
                self.assertTrue(
                    in_window.wait(timeout=STEP_TIMEOUT),
                    "harness: proxy never reached the upstream connect, so the setup window was never entered",
                )
                self.assertTrue(
                    self.proxy.state.redirect_run(trace, f"127.0.0.1:{self.new_port}"), "redirect_run refused the Run"
                )
            finally:
                release.set()
            worker.join(timeout=HOLD_TIMEOUT + STEP_TIMEOUT)

        self.assertFalse(worker.is_alive(), "harness: client thread hung")
        self.assertNotIn("error", result, result.get("error"))

        # Steps 2 and 1: the window tunnel is refused or dead, and the
        # old destination received nothing from it.
        window_sock = result["sock"]
        try:
            if result["status"] == 200:
                # Opened after the Redirect: must already be cut.
                self.assert_tunnel_dead(window_sock)
            else:
                # Refused instead: must be the 503 refusal.
                self.assertEqual(result["status"], 503, "a tunnel refused after Redirect must be 503")
        finally:
            window_sock.close()
        with self.old_dest.count_lock:
            old_bytes = self.old_dest.bytes_in
        self.assertEqual(
            old_bytes, 0, "the old destination received bytes from a Run that was already redirected away from it"
        )

        # Step 3: the Run is steered, not stuck. A retry asks for A
        # again and must land on C, with A still untouched.
        retry_sock, retry_status = self.open_tunnel(trace)
        try:
            self.assertEqual(retry_status, 200, "a retry on a redirected Run must open, not stay refused")
            retry_sock.settimeout(STEP_TIMEOUT)
            retry_sock.sendall(b"ping-on-new-route")
            self.assertEqual(
                retry_sock.recv(65536), b"ping-on-new-route", "the retry tunnel did not echo from the new destination"
            )
        finally:
            retry_sock.close()
        with self.new_dest.count_lock:
            new_bytes = self.new_dest.bytes_in
        with self.old_dest.count_lock:
            old_bytes_after = self.old_dest.bytes_in
        self.assertEqual(new_bytes, len(b"ping-on-new-route"), "the retry did not reach the new destination")
        self.assertEqual(old_bytes_after, 0, "the retry leaked bytes to the old destination")


if __name__ == "__main__":
    unittest.main()
