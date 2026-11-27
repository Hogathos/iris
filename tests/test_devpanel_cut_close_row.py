"""Test 1e: a cut tunnel's close row is not a panel event.

Test-first. Two of the four tests below are expected to FAIL on the
branch with fix A applied (usage-accounting). Charles reviews this test
first. Nothing is fixed until he does.

Problem in one sentence: devpanel.states._event_for maps every journal
row with outcome paused to a Pause event and every row with outcome
redirected to a Redirect event, although its own docstring says control
rows are the events and traffic rows are not. Under fix A a tunnel cut by
Pause or Redirect journals its own close row with that outcome, so if a
human resume is journaled BEFORE that close row, the panel folds the
close row as a new Pause or Redirect and shows the Run as paused or
redirected again, when the human just let it go.

Why it is low risk, and why it is still worth a test: a human resume
takes seconds, the close row lands in milliseconds, so the real order is
control row, close row, resume, and that folds correctly. But the close
row is written on the handler's own thread, and nothing orders it before
a resume. The same flip already exists today on the error path (a cut
that the relay sees as an error is journaled as a steer row), at a low
rate. Fix A makes the close row the usual case, so the panel should
not depend on thread timing. A run state is a pure function of the
journal. It must not change because a thread was slow.

Decision, dated 2026-11-20, made by Charles: the distinguishing signal
is the row's reason. Fix A journals a cut tunnel's close row with the
reason "tunnel cut by pause" or "tunnel cut by redirect". Control rows
keep "human pause", "Steer pause" and the like. Rejected: the
close_reason field, because _event_for does not read it today. Rejected:
bytes or method, because an idle cut tunnel has 0 bytes and the sweep row
uses method CONNECT. So the reader can tell a close row from a control
row by reading reason, the way it already reads reason to spot a cap
block.

What the four tests pin, in plain words:
  1. A cut tunnel's close row that lands after the human resume does not
     put the Run back to paused or redirected. (Fails today.)
  2. The same, with the close row taken from a real proxy run, so the
     writer's reason and the reader's rule cannot drift apart. The rows
     are real and only the order is arranged: control row, resume,
     close row. (Fails today.)
  3. A second human Pause after a resume still pauses the Run. The fix
     must not make the panel ignore every paused row. Discriminate, do
     not blur. (Passes today and must keep passing.)
  4. The order the journal really has, control row, close row, resume,
     folds to FLOWING. (Passes today and must keep passing.)

This test does not say how the reader recognizes the close row. It pins
what the panel concludes. It uses the reason strings above only to build
the close row, because that is the contract fix A wrote.

Loopback only. Metadata only. No bodies, no keys, no real hostnames.
"""

import os
import socket
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import proxy as proxy_module
from devpanel import states as states_module

# How long the test waits for each step before it fails loudly.
STEP_TIMEOUT = 5
HOLD_TIMEOUT = 10
# The tunnel carries this payload each way before the cut.
PAYLOAD = b"hello"


def control_row(outcome, reason):
    """A control row as pause_run, redirect_run journal it. Status unknown."""
    return {"outcome": outcome, "reason": reason, "status": "unknown", "close_reason": outcome, "method": "", "bytes_tx": 0}


def resume_row(kind):
    """The resume row retry_run journals: outcome empty, reason fixed."""
    return {
        "outcome": "",
        "reason": "retry allowed after verify",
        "status": "unknown",
        "close_reason": kind,
        "method": "RETRY",
        "bytes_tx": 0,
    }


def close_row(outcome, kind):
    """A cut tunnel's own close row, as fix A journals it."""
    return {
        "outcome": outcome,
        "reason": f"tunnel cut by {kind}",
        "status": "unknown",
        "close_reason": outcome,
        "method": "CONNECT",
        "bytes_tx": 5,
    }


class _EchoHandler(socketserver.BaseRequestHandler):
    """Loopback echo upstream. Test helper only, never a real destination."""

    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class CutCloseRowIsNotAnEventTest(unittest.TestCase):
    """The panel's run state does not depend on when a close row lands."""

    # (kind, outcome of its control and close rows, state while it holds)
    CASES = (
        ("pause", "paused", "human pause", "PAUSED"),
        ("redirect", "redirected", "human redirect", "REDIRECTED"),
    )

    def test_late_close_row_after_resume_does_not_flip_the_run(self):
        """Check 1: the journal order the proxy does not promise."""
        for kind, outcome, why, held_state in self.CASES:
            with self.subTest(kind=kind):
                rows = [control_row(outcome, why), resume_row(outcome)]
                self.assertEqual(
                    states_module.run_state(rows), "FLOWING", "harness: the resume must return the Run to FLOWING"
                )
                rows.append(close_row(outcome, kind))
                self.assertEqual(
                    states_module.run_state(rows),
                    "FLOWING",
                    f"a cut tunnel's close row landed after the human resume and put the Run back to {held_state}",
                )

    def test_real_close_row_after_resume_does_not_flip_the_run(self):
        """Check 2: the same, with the close row from a real proxy run."""
        tmp = tempfile.TemporaryDirectory()
        echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        threading.Thread(target=echo.serve_forever, daemon=True).start()
        db_path = os.path.join(tmp.name, "attempts.db")
        # High rate limit: a 429 here would mask the tunnel this opens.
        proxy = proxy_module.serve("127.0.0.1", 0, db_path, rate_limit=10000)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        trace = "bench-devpanel-cut-close-row-1"
        dest = f"127.0.0.1:{echo.server_address[1]}"
        sock = None
        try:
            sock = socket.create_connection(("127.0.0.1", proxy.server_address[1]), timeout=HOLD_TIMEOUT)
            sock.settimeout(HOLD_TIMEOUT)
            sock.sendall((f"CONNECT {dest} HTTP/1.1\r\nHost: {dest}\r\nX-Trace-Id: {trace}\r\n\r\n").encode())
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                raw += chunk
            self.assertIn(b" 200 ", raw.split(b"\r\n", 1)[0], "harness: the tunnel did not open")
            sock.sendall(PAYLOAD)
            self.assertEqual(sock.recv(65536), PAYLOAD, "harness: the tunnel did not echo")
            self.assertTrue(proxy.state.pause_run(trace), "pause_run refused the Run")
            # The close row is the Run's row that carries the payload.
            rows = self.wait_for_rows(db_path, trace)
        finally:
            if sock is not None:
                sock.close()
            proxy.shutdown()
            echo.shutdown()
            proxy.server_close()
            echo.server_close()
            proxy.state.conn.close()
            tmp.cleanup()
        control = [r for r in rows if r["bytes_tx"] == 0 and r["outcome"] == "paused"][0]
        real_close = [r for r in rows if r["bytes_tx"] == len(PAYLOAD)][0]
        # The two real rows, with the resume placed between them: the
        # order a slow handler thread could produce.
        late = [control, resume_row("paused"), real_close]
        self.assertEqual(
            states_module.run_state(late),
            "FLOWING",
            "the real close row of a cut tunnel put the Run back to paused after the human resume",
        )

    def test_second_human_pause_after_resume_still_pauses(self):
        """Check 3, the control: do not make the panel ignore paused rows."""
        for kind, outcome, why, held_state in self.CASES:
            with self.subTest(kind=kind):
                rows = [control_row(outcome, why), resume_row(outcome), control_row(outcome, why)]
                self.assertEqual(
                    states_module.run_state(rows),
                    held_state,
                    "a second human control row after a resume must still move the Run",
                )

    def test_close_row_before_resume_folds_as_before(self):
        """Check 4, the control: the order the journal really has."""
        for kind, outcome, why, held_state in self.CASES:
            with self.subTest(kind=kind):
                rows = [control_row(outcome, why), close_row(outcome, kind)]
                self.assertEqual(states_module.run_state(rows), held_state, "the Run stays held until the human resume")
                rows.append(resume_row(outcome))
                self.assertEqual(states_module.run_state(rows), "FLOWING")

    # ---- harness -------------------------------------------------------

    def wait_for_rows(self, db_path, trace):
        """Wait for the control row and the tunnel's close row. Read only.

        The close row lands on the handler's thread after pause_run
        returns. Polls with a deadline and fails loudly.
        """
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = [dict(r) for r in conn.execute("SELECT * FROM attempts WHERE trace_id = ? ORDER BY id", (trace,))]
            finally:
                conn.close()
            has_control = any(r["bytes_tx"] == 0 and r["outcome"] == "paused" for r in rows)
            has_close = any(r["bytes_tx"] == len(PAYLOAD) for r in rows)
            if has_control and has_close:
                return rows
            time.sleep(0.02)
        self.fail("the control row or the tunnel's close row never reached the journal")


if __name__ == "__main__":
    unittest.main()
