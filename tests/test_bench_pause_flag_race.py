"""Bench Part B, test 1c: the Pause pop-to-flag race.

Test-first. This test is expected to FAIL on 0e6b829 (main plus the 1b
fix). A failure is the proof that a tunnel can slip past a Pause.
Charles reviews this test first. Nothing is fixed until he does.

Problem in one sentence: pause_run takes the Run's live tunnels, then
journals, and only then sets the paused flag, so a tunnel that registers
in between is not in the set Pause cuts, and it also passes the paused
re-check in do_CONNECT because the flag is not set yet, so nothing ever
cuts it and a paused Run keeps a working tunnel.

The order inside pause_run (proxy.py), from reading it:
  1. lock: pop the Run's live tunnels            <- the set to cut
  2. journal: save_paused, then the pause row    <- no lock held
  3. lock: set the paused flag                   <- do_CONNECT sees this
  4. shut the popped tunnels
A tunnel that registers between 1 and 3 is in neither set: not popped,
and the re-check does not see the flag yet. Unverified until this test
runs and fails.

How the gap is held open, deterministically, no sleeps and no luck:
the test wraps db.save_paused, the first call pause_run makes after the
pop (step 2), so that it stops at a gate the test controls. While
pause_run is stopped there, the test opens a tunnel through the proxy.
That tunnel registers after the pop and before the flag. Then the test
releases the gate and lets pause_run finish.

What must be true afterwards, either outcome is correct, because the
fix may choose where to close the gap:
  - the proxy refused the tunnel with 503 (the flag was already in
    place), or
  - the tunnel opened and is cut by the time pause_run returns, so it
    carries no more bytes: EOF or error, never echo. A timeout means
    still alive, and that fails loudly.

The test waits for pause_run to return before it sends on the tunnel,
so a cut that lands is never raced by the send.

This test does not check how many rows the journal holds, or which
step the fix moves. It checks the tunnel only.

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
# How long the held journal step waits for release before it gives up.
# Longer than STEP_TIMEOUT so a slow machine never turns the gate into
# a false failure.
HOLD_TIMEOUT = 10


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class PausePopToFlagRaceTest(unittest.TestCase):
    """A tunnel registering between Pause's pop and its flag."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.echo = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _EchoHandler)
        cls.echo_port = cls.echo.server_address[1]
        threading.Thread(target=cls.echo.serve_forever, daemon=True).start()
        # High rate limit: rate limiting has its own tests, and a 429
        # here would mask the tunnel this test needs to open.
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

    def open_tunnel(self, trace):
        """CONNECT through the proxy for this Run. Returns (sock, status)."""
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
            "A tunnel registered after Pause took the live tunnels and "
            "before Pause set its flag, and the tunnel carries bytes "
            "anyway: nothing cut it")

    def test_tunnel_between_pause_pop_and_flag_is_cut(self):
        trace = "bench-pause-flag-race-1"
        in_journal = threading.Event()   # pause_run is past the pop
        release = threading.Event()      # test lets pause_run finish
        real_save_paused = proxy_module.db_module.save_paused

        def held_save_paused(conn, trace_id, *args, **kwargs):
            # Hold only this Run's pause, and only the first call. This
            # is the first thing pause_run does after the pop and
            # before the flag, with no lock held.
            if trace_id == trace and not in_journal.is_set():
                in_journal.set()
                if not release.wait(timeout=HOLD_TIMEOUT):
                    raise OSError("test harness: journal hold never released")
            return real_save_paused(conn, trace_id, *args, **kwargs)

        pause_result = {}

        def run_pause():
            try:
                pause_result["ok"] = self.proxy.state.pause_run(trace)
            except Exception as exc:  # reported by the assertions below
                pause_result["error"] = repr(exc)

        tunnel_sock = None
        with mock.patch.object(
                proxy_module.db_module, "save_paused", held_save_paused):
            pauser = threading.Thread(target=run_pause, daemon=True)
            pauser.start()
            try:
                self.assertTrue(
                    in_journal.wait(timeout=STEP_TIMEOUT),
                    "harness: pause_run never reached the journal step, "
                    "so the pop-to-flag gap was never entered")
                # pause_run has popped the live tunnels and has not set
                # its flag. Open the tunnel now: it registers in the gap.
                tunnel_sock, status = self.open_tunnel(trace)
            finally:
                release.set()
            pauser.join(timeout=HOLD_TIMEOUT + STEP_TIMEOUT)

        try:
            self.assertFalse(pauser.is_alive(), "harness: pause_run hung")
            self.assertNotIn("error", pause_result, pause_result.get("error"))
            self.assertTrue(pause_result.get("ok"), "pause_run refused the Run")
            # pause_run has returned, so any cut it was going to make
            # has landed before the test sends a single byte.
            if status == 200:
                self.assert_tunnel_dead(tunnel_sock)
            else:
                self.assertEqual(
                    status, 503,
                    "a tunnel refused during Pause must be 503 (paused)")
        finally:
            if tunnel_sock is not None:
                tunnel_sock.close()


if __name__ == "__main__":
    unittest.main()
