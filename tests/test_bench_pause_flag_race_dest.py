"""Bench Part B, test 1d: the Pause row names a tunnel the sweep cuts.

Test-first. This test is expected to FAIL on fd906b3 (the 1c fix). A
failure is the proof that the Pause row has an empty dest_host when a
tunnel registers between the pop and the flag. Charles reviews this
test first. Nothing is fixed until he does.

Problem in one sentence: pause_run reads the destination in its first
lock section, before the late tunnel exists, and the second sweep finds
that tunnel only after the pause row is written, so the row that records
the cut names no destination.

Why it matters: the journal is the proof of what the human stopped. In
this race the Pause cut a live tunnel, and the row that says so cannot
say where the tunnel was going. Test 1b pinned the same rule for a
tunnel that is still connecting. Part A pinned it for a tunnel that is
already live. This test pins it for the last case, a tunnel that went
live in the gap.

The order inside pause_run (proxy.py), from reading it:
  1. lock: pop the live tunnels, read pending destinations
  2. read the Run's last journal row, pick dest_host and endpoint
  3. journal: save_paused, then the pause row (uses the values from 2)
  4. lock: set the flag, second sweep for late tunnels (the 1c fix)
  5. shut the popped and swept tunnels
A tunnel that registers between 1 and 4 is cut by the sweep. Step 2 has
already run, so the row cannot name it. Unverified until this test runs
and fails.

How the gap is held open, deterministically, no sleeps and no luck:
the same harness as test 1c. The test stops pause_run at db.save_paused
(step 3), opens a tunnel through the proxy while pause_run is stopped,
then releases pause_run and waits for it to return. The pause row is
written before pause_run returns, so the test reads the journal at that
point and there is nothing to poll.

What must be true: every pause row for the Run names the destination of
the tunnel the Pause cut, in the same form Part A uses for an open
tunnel ("127.0.0.1:<port>"), never an empty string.

This test needs the tunnel to be live in the gap. It asserts that the
proxy answered 200 before the release. If a later change makes the proxy
refuse a tunnel in the gap, then no tunnel is cut, this test no longer
applies, and it fails loudly with that message so a human can decide.

This test does not check what the tunnel does after the Pause. Test 1c
covers that. It checks the journal row only. The row's endpoint column
is empty today for the same reason. This test pins dest_host only, so
the fix is free to choose how to fill endpoint.

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


class PauseRowNamesSweptTunnelTest(unittest.TestCase):
    """The Pause row names the destination of a tunnel the sweep cuts."""

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

    def test_pause_row_names_the_tunnel_the_sweep_cuts(self):
        trace = "bench-pause-flag-race-dest-1"
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
        status = None
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
            self.assertEqual(
                status, 200,
                "harness: the tunnel must be live in the gap. If the proxy "
                "now refuses it there, no tunnel is cut and test 1d no "
                "longer applies: review this test")
            # pause_run has returned. Its pause row is written. Read the
            # journal now. A row the proxy writes later can only add to
            # the rows, so this check cannot be raced.
            pause_rows = [r for r in self.rows_for(trace)
                          if r["outcome"] == "paused"]
            self.assertTrue(
                pause_rows, "pause_run returned but journaled no pause row")
            for row in pause_rows:
                self.assertEqual(
                    row["dest_host"], self.dest,
                    "Pause cut a tunnel that registered between the pop "
                    "and the flag, and the pause row does not name the "
                    "destination of that tunnel")
        finally:
            if tunnel_sock is not None:
                tunnel_sock.close()


if __name__ == "__main__":
    unittest.main()
