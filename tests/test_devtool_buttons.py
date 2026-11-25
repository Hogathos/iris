"""Devtool buttons test (test-first, failing until built).

Problem in one sentence: the dev panel must offer the four
remaining Steer controls as buttons, each calling the proxy setter
that already exists, in the Pause-button shape, plus a small
styling pass that keeps the panel readable.

Done clause for this test Run (human-approved, once per task type,
as a named read-back, before the Run starts):
  task_type = "local file write"
  clause    = "row exists after close/fsync"
  read_back = "named read-back: file row exists after close/fsync"
No approved done = Unverified, never Correct.

Expected failure right now: the panel has no redirect, cap,
block-network, or emergency-stop route, and no style block. That
failure is correct. It is the test-first proof. Charles reviews
this test with the finished work, because he chose one prompt
for the whole change ("All of it, until you need me").

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames.
"""

import os
import socket
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import proxy as proxy_module
from devpanel import panel as panel_module


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class DevtoolButtonsTest(unittest.TestCase):
    """Redirect, cap, block-network, emergency stop, plus the look."""

    def setUp(self):
        """Fresh proxy, fresh temp DB, two loopback destinations."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "attempts.db")
        self.echoA = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        self.echoB = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        threading.Thread(target=self.echoA.serve_forever, daemon=True).start()
        threading.Thread(target=self.echoB.serve_forever, daemon=True).start()
        self.proxy = proxy_module.serve("127.0.0.1", 0, self.db_path)
        self.proxy_port = self.proxy.server_address[1]
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        # addCleanup runs LIFO: shutdowns are added last so they run
        # first then closes, then DB, then temp. Closing a listening
        # socket while serve_forever still selects on it crashes
        # Windows; shutdown first, always.
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.proxy.state.conn.close)
        self.addCleanup(self.echoA.server_close)
        self.addCleanup(self.echoB.server_close)
        self.addCleanup(self.proxy.server_close)
        self.addCleanup(self.echoA.shutdown)
        self.addCleanup(self.echoB.shutdown)
        self.addCleanup(self.proxy.shutdown)
        self.destA = f"127.0.0.1:{self.echoA.server_address[1]}"
        self.destB = f"127.0.0.1:{self.echoB.server_address[1]}"
        assert self.destA != self.destB

    def _request(self, state):
        """A fake panel request that carries the live proxy state."""
        return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(proxy_state=state)))

    def _rows_for(self, trace_id):
        """All journal rows for one trace_id, oldest first. Read only."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM attempts WHERE trace_id = ? ORDER BY id", (trace_id,))]
        finally:
            conn.close()

    def _wait_for_rows(self, trace_id, timeout=5.0):
        """Journal rows land when a tunnel closes; wait a little."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self._rows_for(trace_id)
            if rows:
                return rows
            time.sleep(0.05)
        return self._rows_for(trace_id)

    def open_tunnel(self, dest, trace_id):
        """Open one raw CONNECT tunnel; return (socket, status code)."""
        sock = socket.create_connection(("127.0.0.1", self.proxy_port), timeout=5)
        sock.settimeout(5)
        sock.sendall((f"CONNECT {dest} HTTP/1.1\r\nHost: {dest}\r\nX-Trace-Id: {trace_id}\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            self.assertTrue(chunk, "proxy never answered CONNECT")
            raw += chunk
        head = raw.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
        return sock, int(head.split()[1])

    def echo_roundtrip(self, sock, payload):
        """Send bytes through a tunnel, expect the same bytes back."""
        sock.sendall(payload)
        got = b""
        while len(got) < len(payload):
            chunk = sock.recv(65536)
            if not chunk:
                break
            got += chunk
        return got

    def assert_tunnel_dead(self, sock):
        """A cut tunnel carries no more bytes: EOF or error, never an echo."""
        sock.settimeout(5)
        try:
            sock.sendall(b"ping-after-cut")
        except socket.timeout:
            self.fail("tunnel send timed out: cut did not take effect")
        except OSError:
            return
        try:
            data = sock.recv(65536)
        except socket.timeout:
            self.fail("tunnel recv timed out: cut did not take effect")
        except OSError:
            return
        self.assertEqual(data, b"", "tunnel still flows after cut")

    def test_redirect_route_moves_run_and_refuses_bad_dest(self):
        trace = "devtool-buttons-redirect"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            request = self._request(self.proxy.state)
            resp = panel_module.redirect_button(trace, request, new_dest=self.destB)
            self.assertEqual(resp.status_code, 303)
            self.assertEqual(self.proxy.state.redirect_target(trace), self.destB)
            self.assert_tunnel_dead(sock)
            bad = panel_module.redirect_button(trace, request, new_dest="example.com:443")
            self.assertEqual(bad.status_code, 400)
            self.assertIn("refused", bad.body.decode().lower())
            self.assertEqual(self.proxy.state.redirect_target(trace), self.destB)
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_cap_route_sets_cap_and_refuses_bad_value(self):
        panel_module.app.state.proxy_state = self.proxy.state
        request = self._request(self.proxy.state)
        try:
            resp = panel_module.cap_button(request, dest=self.destA, dollars="10")
            self.assertEqual(resp.status_code, 303)
            rule = self.proxy.state.cap_rule(self.destA)
            self.assertIsNotNone(rule)
            self.assertEqual(rule["cap"], 10.0)
            rows = [r for r in self._rows_for("") if r["reason"] == "cap set $10.00"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["cost_dollars"], 0.0)
            bad = panel_module.cap_button(request, dest=self.destA, dollars="abc")
            self.assertEqual(bad.status_code, 400)
            self.assertIn("refused", bad.body.decode().lower())
            self.assertEqual(self.proxy.state.cap_rule(self.destA)["cap"], 10.0)
            bad_dest = panel_module.cap_button(request, dest="example.com:443", dollars="10")
            self.assertEqual(bad_dest.status_code, 400)
            self.assertIn("refused", bad_dest.body.decode().lower())
        finally:
            panel_module.app.state.proxy_state = None

    def test_block_network_route_blocks_and_refuses_bad_dest(self):
        trace = "devtool-buttons-block"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            request = self._request(self.proxy.state)
            resp = panel_module.block_network_button(request, dest=self.destA)
            self.assertEqual(resp.status_code, 303)
            rule = self.proxy.state.override_rule(self.destA)
            self.assertIsNotNone(rule)
            self.assertEqual(rule["action"], "pause")
            self.assert_tunnel_dead(sock)
            bad = panel_module.block_network_button(request, dest="example.com:443")
            self.assertEqual(bad.status_code, 400)
            self.assertIn("refused", bad.body.decode().lower())
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_emergency_stop_confirm_then_execute(self):
        trace = "devtool-buttons-stop"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            request = self._request(self.proxy.state)
            confirm = panel_module.emergency_stop_confirm(request)
            self.assertEqual(confirm.status_code, 200)
            self.assertIn("Let agents work again?", confirm.body.decode())
            self.assertIn("Runs that were stopped stay stopped.", confirm.body.decode())
            resp = panel_module.emergency_stop_execute(request)
            self.assertEqual(resp.status_code, 303)
            self.assert_tunnel_dead(sock)
            stopped = [r for r in self._rows_for(trace) if r["outcome"] == "stopped"]
            self.assertTrue(stopped, "stop press must be journaled")
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_panel_routes_watch_only_without_proxy(self):
        request = self._request(None)
        panel_module.app.state.proxy_state = None
        try:
            resp = panel_module.redirect_button("x", request, new_dest=self.destA)
            self.assertEqual(resp.status_code, 503)
            self.assertIn("watching only", resp.body.decode().lower())
            resp = panel_module.cap_button(request, dest=self.destA, dollars="10")
            self.assertEqual(resp.status_code, 503)
            resp = panel_module.block_network_button(request, dest=self.destA)
            self.assertEqual(resp.status_code, 503)
            resp = panel_module.emergency_stop_execute(request)
            self.assertEqual(resp.status_code, 503)
            resp = panel_module.emergency_stop_confirm(request)
            self.assertEqual(resp.status_code, 503)
        finally:
            panel_module.app.state.proxy_state = None

    def test_panel_index_shows_new_sections_and_forms(self):
        old_db_path = panel_module.api_module.DB_PATH
        try:
            panel_module.api_module.DB_PATH = self.db_path
            panel_module.app.state.proxy_state = self.proxy.state
            html = panel_module.index(self._request(self.proxy.state))
            self.assertIn("Emergency stop", html)
            self.assertIn("<style>", html)
            limits = panel_module.limits_page(self._request(self.proxy.state))
            self.assertIn("Spending.", limits)
            self.assertIn('name="daily"', limits)
            self.assertIn("Blocked places.", limits)
            self.assertIn('action="/settings/block-network"', limits)
            tools = panel_module.tools_page(self._request(self.proxy.state))
            self.assertIn("Run the demo", tools)
            self.assertIn("Log detail", tools)
            self.assertIn("Watching", tools)
        finally:
            panel_module.api_module.DB_PATH = old_db_path
            panel_module.app.state.proxy_state = None

    def test_panel_run_page_shows_redirect_form(self):
        trace = "devtool-buttons-run-page"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            sock.close()
            self.assertTrue(self._wait_for_rows(trace), "run needs a journal row before its page renders")
            old_db_path = panel_module.api_module.DB_PATH
            try:
                panel_module.api_module.DB_PATH = self.db_path
                panel_module.app.state.proxy_state = self.proxy.state
                html = panel_module.run_detail(trace)
                self.assertIn('name="new_dest"', html)
                self.assertIn("Send this Run somewhere else", html)
            finally:
                panel_module.api_module.DB_PATH = old_db_path
                panel_module.app.state.proxy_state = None
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
