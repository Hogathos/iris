"""Devtool Verify button test (test-first, failing until built).

Problem in one sentence: the dev panel shows a paused or redirected
Run and its next step, but the human has no button to Verify it, so
the retry gate stays shut even after the human checked.

Done clause for this test Run (human-approved, once per task type,
as a named read-back, before the Run starts):
  task_type = "local file write"
  clause    = "row exists after close/fsync"
  read_back = "named read-back: file row exists after close/fsync"
No approved done = Unverified, never Correct.

Expected failure right now: the panel has no Verify route and no
Verify form on the Run page. That failure is correct. It is the
test-first proof. Charles reviews this test with the finished work,
because he chose one prompt for the whole change ("pull merge
push").

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

import api as api_module

READ_BACK = "named read-back: file row exists after close/fsync"


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class DevtoolVerifyButtonTest(unittest.TestCase):
    """Verify button: stamp the gate for paused and redirected Runs."""

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
        # The Run page reads the journal through the panel's own DB
        # path, so point it at this test's temp DB.
        self.old_db_path = api_module.DB_PATH
        api_module.DB_PATH = self.db_path
        self.addCleanup(setattr, api_module, "DB_PATH", self.old_db_path)

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

    def test_verify_stamps_paused_run_and_retry_opens(self):
        """Verify on a paused Run stamps the gate; retry is allowed."""
        trace = "devtool-verify-paused"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            request = self._request(self.proxy.state)
            self.assertTrue(self.proxy.state.pause_run(trace))
            self.assert_tunnel_dead(sock)
            resp = panel_module.verify_button(trace, request, read_back=READ_BACK)
            self.assertEqual(resp.status_code, 303)
            allowed, why = self.proxy.state.retry_run(trace)
            self.assertTrue(allowed, f"retry stayed shut: {why}")
            rows = self._rows_for(trace)
            self.assertTrue(
                any(r["reason"].startswith("verify ok") and READ_BACK in r["reason"] for r in rows),
                "no verify ok row carrying the read-back in the journal",
            )
            self.assertTrue(any(r["reason"] == "retry allowed after verify" for r in rows), "no retry row in the journal")
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_verify_stamps_redirected_run(self):
        """Verify on a redirected Run stamps the gate too."""
        trace = "devtool-verify-redirected"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            request = self._request(self.proxy.state)
            self.assertTrue(self.proxy.state.pause_run(trace))
            self.assertTrue(self.proxy.state.redirect_run(trace, self.destB))
            resp = panel_module.verify_button(trace, request, read_back=READ_BACK)
            self.assertEqual(resp.status_code, 303)
            allowed, why = self.proxy.state.retry_run(trace)
            self.assertTrue(allowed, f"retry stayed shut: {why}")
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_verify_refused_when_run_flowing(self):
        """Verify on a flowing Run is refused; nothing is stamped."""
        trace = "devtool-verify-flowing"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            request = self._request(self.proxy.state)
            resp = panel_module.verify_button(trace, request, read_back=READ_BACK)
            self.assertEqual(resp.status_code, 400)
            self.assertIn("refused", resp.body.decode().lower())
            allowed, why = self.proxy.state.retry_run(trace)
            self.assertTrue(allowed, "flowing run must stay ungated")
            self.assertEqual(why, "not paused")
            rows = self._rows_for(trace)
            self.assertFalse(any(r["reason"].startswith("verify ok") for r in rows), "a refused Verify must stamp nothing")
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_verify_refused_when_read_back_empty(self):
        """An empty read-back is refused; nothing is stamped."""
        trace = "devtool-verify-empty"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            request = self._request(self.proxy.state)
            self.assertTrue(self.proxy.state.pause_run(trace))
            self.assert_tunnel_dead(sock)
            resp = panel_module.verify_button(trace, request, read_back="   ")
            self.assertEqual(resp.status_code, 400)
            self.assertIn("refused", resp.body.decode().lower())
            allowed, _why = self.proxy.state.retry_run(trace)
            self.assertFalse(allowed, "empty read-back must not open retry")
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_verify_form_shown_only_for_paused_or_redirected(self):
        """Run page shows the form for a paused Run, not a flowing one."""
        trace = "devtool-verify-form"
        sock, status = self.open_tunnel(self.destA, trace)
        self.assertEqual(status, 200)
        try:
            self.assertEqual(self.echo_roundtrip(sock, b"run-on-A"), b"run-on-A")
            panel_module.app.state.proxy_state = self.proxy.state
            flowing_page = panel_module.run_detail(trace)
            flowing_body = flowing_page if isinstance(flowing_page, str) else flowing_page.body.decode()
            self.assertNotIn(f"/runs/{trace}/verify", flowing_body, "a flowing Run must show no Verify button")
            self.assertTrue(self.proxy.state.pause_run(trace))
            self.assert_tunnel_dead(sock)
            paused_page = panel_module.run_detail(trace)
            paused_body = paused_page if isinstance(paused_page, str) else paused_page.body.decode()
            self.assertIn(f"/runs/{trace}/verify", paused_body)
            self.assertIn('name="read_back"', paused_body)
        finally:
            panel_module.app.state.proxy_state = None
            sock.close()

    def test_verify_watch_only_503(self):
        """With no proxy beside the panel, the route is watch-only."""
        request = self._request(None)
        resp = panel_module.verify_button("devtool-verify-watch-only", request, read_back=READ_BACK)
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
