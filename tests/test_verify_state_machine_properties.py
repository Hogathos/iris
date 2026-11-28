"""Property tests over the Iris state machine, placed with the library.

Test-first: this file FAILS until verify_library exists. The failure is
the missing feature (ModuleNotFoundError for verify_library), not a
typo: the machine itself lives in devpanel/states.py, and the library
is the feature under test.

The contract these tests pin (the implementer builds to it):
  verify_library.STATES -> the run states, exactly the machine's.
  verify_library.TRANSITIONS -> (state, event) -> next state, exactly
      the machine's.
  verify_library.step(state, event) -> the next state for one event,
      or the same state when the event moves nothing (the machine's
      TRANSITIONS.get((state, event), state) rule). An invalid state
      raises ValueError (N4, D7).
The library's model must match the real machine exactly: a model that
omits a transition, or adds an invalid one, fails here. That is the
point -- a test that proves the implementation wrong.

Deterministic exhaustive enumeration, no randomness except a
fixed-seed soup (N5). Hypothesis is a CI test dependency (the
workflow installs it, and test_bench_properties.py uses it), but it
is not installed in this environment, and for a finite explicit
transition table enumeration is a complete proof, stronger than
sampling: every event sequence up to length 4 is folded, every
transition is exercised from a shortest path, and reachability is
proved by breadth-first search over the whole graph.

Two parts. The pure part folds event sequences through the library
model and the real machine over synthetic journal rows. The real part
(RealJournalRowShapesTest) produces rows through the proxy's real
API on a fresh trace and asserts _event_for reads them as the right
event: no network, the state is built directly, no server socket.

Charles's framing: these tests are based on the evidence outlined and
as verifiable as they can be. If something breaks, that is a discovery,
not a defect.
"""

import collections
import itertools
import os
import random
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# urllib honors NO_PROXY and would bypass the loopback test proxy;
# drop it so the suite never depends on ambient proxy config.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import proxy as proxy_module
import verify_library as library_module
from devpanel import states as states_module

# One synthetic journal row per machine event, in the shapes
# devpanel/states._event_for recognizes. Traffic rows map to no event.
ROW_FOR_EVENT = {
    "pause": {"outcome": "paused", "status": "unknown", "reason": "Steer pause"},
    "redirect": {"outcome": "redirected", "status": "unknown", "reason": ""},
    "red_button": {"outcome": "stopped", "status": "unknown", "reason": ""},
    "cap_block": {"outcome": "paused", "status": "unknown", "reason": "cap block at 100%"},
    "cap_warn": {"outcome": "warned", "status": "unknown", "reason": ""},
    "verify_ok": {"outcome": "verified", "status": "success", "reason": "verify Correct"},
    "resume": {"outcome": "", "status": "unknown", "reason": "retry allowed after verify"},
}
TRAFFIC_ROW = {"outcome": "allowed", "status": "success", "reason": ""}

# Every row shape _event_for must recognize, with the event it maps
# to, or None when the row moves nothing (H10). This is the table the
# recognizer is tested against: one row per shape, including the
# shapes the single-row-per-event table above never exercises.
ROW_SHAPES = [
    # A cut tunnel's close row moves nothing, whatever its outcome.
    ({"outcome": "paused", "status": "unknown", "reason": "tunnel cut by pause"}, None),
    ({"outcome": "stopped", "status": "unknown", "reason": "tunnel cut by red button"}, None),
    # A Verify that failed is not an event: only a success verifies.
    ({"outcome": "verified", "status": "failure", "reason": "verify Incorrect"}, None),
    # The call row and an Unverified verdict row are Verify talking
    # about itself, not an event.
    ({"outcome": "verified", "status": "unknown", "reason": "verify call lib-x-1"}, None),
    ({"outcome": "verified", "status": "unknown", "reason": "verify Unverified"}, None),
    # A Verify success is the verify_ok event.
    ({"outcome": "verified", "status": "success", "reason": "verify Correct"}, "verify_ok"),
    # The retry and cap-lift rows resume the Run.
    ({"outcome": "", "status": "unknown", "reason": "cap lifted"}, "resume"),
    ({"outcome": "", "status": "unknown", "reason": "retry allowed after verify"}, "resume"),
    # A Verify after a pause is verify_ok, never a resume.
    ({"outcome": "", "status": "unknown", "reason": "verify ok: file row exists"}, "verify_ok"),
    # A missing reason is an empty reason, not an error.
    ({"outcome": "paused", "status": "unknown", "reason": None}, "pause"),
    ({"outcome": "", "status": "unknown", "reason": None}, None),
    # A pause whose reason names a cap block is a cap block, not a
    # pause: the match is on free text. A discovery, characterized
    # here, not part of this slice.
    ({"outcome": "paused", "status": "unknown", "reason": "manual cap block note"}, "cap_block"),
]


def fold_library(events):
    """Fold one event sequence through the library's model, from FLOWING."""
    state = "FLOWING"
    for event in events:
        state = library_module.step(state, event)
    return state


def fold_real(events):
    """Fold one event sequence through the real machine, from FLOWING."""
    rows = [dict(ROW_FOR_EVENT[event]) for event in events]
    return states_module.run_state(rows)


def reachable_from(start, transitions):
    """Every state reachable from start, by breadth-first search."""
    seen = {start}
    frontier = collections.deque([start])
    while frontier:
        state = frontier.popleft()
        for (source, _event), target in transitions.items():
            if source == state and target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


def shortest_path(start, target, transitions):
    """Shortest event path from start to target, by breadth-first search.

    Returns a list of events, or None when the target is unreachable.
    """
    queue = collections.deque([(start, [])])
    seen = {start}
    while queue:
        state, path = queue.popleft()
        if state == target:
            return path
        for (source, event), nxt in transitions.items():
            if source == state and nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [event]))
    return None


class StateMachinePropertyTest(unittest.TestCase):
    """The machine: every transition reachable, no invalid state."""

    def test_library_model_matches_real_machine(self):
        """Criterion 5: the library covers exactly the real machine."""
        self.assertEqual(library_module.STATES, states_module.STATES)
        self.assertEqual(library_module.TRANSITIONS, states_module.TRANSITIONS)

    def test_every_transition_target_is_valid(self):
        """Criterion 5: no transition leads outside the states."""
        transitions = library_module.TRANSITIONS
        states = library_module.STATES
        self.assertTrue(transitions, "the machine must have transitions")
        self.assertTrue(states, "the machine must have states")
        events = set()
        for (source, event), target in transitions.items():
            self.assertIn(source, states, f"transition source {source} is not a state")
            self.assertIn(target, states, f"transition target {target} is not a state")
            events.add(event)
        self.assertEqual(
            events,
            {"pause", "redirect", "red_button", "cap_block", "cap_warn", "verify_ok", "resume"},
            "the event alphabet is exactly the Steer actions plus resume",
        )

    def test_every_state_reachable_from_flowing(self):
        """Criterion 5: every state is reachable from the start state."""
        reached = reachable_from("FLOWING", library_module.TRANSITIONS)
        for state in library_module.STATES:
            self.assertIn(state, reached, f"{state} is unreachable from FLOWING")

    def test_every_transition_is_exercised_and_lands_on_target(self):
        """Criterion 5 (H10): every transition is exercised, not just every state.

        For each (state, event) in the machine, take a shortest path
        from FLOWING to that state, append the event, and fold the
        walk through both the library model and the real machine.
        Both must land on the transition's declared target. The
        diameter -- the longest shortest path from FLOWING -- plus
        one step must fit inside the exhaustive walk's length 4 (N2).
        """
        transitions = states_module.TRANSITIONS
        for (source, event), target in sorted(transitions.items()):
            path = shortest_path("FLOWING", source, transitions)
            self.assertIsNotNone(path, f"no path from FLOWING to {source}")
            walk = path + [event]
            self.assertEqual(fold_library(walk), target, f"library misses {source} + {event} -> {target}")
            self.assertEqual(fold_real(walk), target, f"real machine misses {source} + {event} -> {target}")
        diameter = max(len(shortest_path("FLOWING", s, transitions)) for s in states_module.STATES)
        self.assertLessEqual(diameter + 1, 4, "the walk length must clear the diameter plus one")

    def test_no_invalid_state_on_exhaustive_walks(self):
        """Criterion 5: every walk of the machine stays inside the states.

        All event sequences up to length 4, folded twice: once through
        the library's model, once through the real machine over
        synthetic journal rows. The two folds must agree, and neither
        may ever leave the states. The alphabet comes from the real
        machine (N3), not the model, so a model that drops an event
        cannot shrink the walk. Every (state, event) pair the walk
        visits is recorded, and the visited set must cover all 32
        defined pairs and all 17 no-op pairs (H10): coverage is
        asserted, not assumed. The walk count is exact (N2), not a
        lower bound.
        """
        states = library_module.STATES
        events = sorted({event for (_source, event) in states_module.TRANSITIONS})
        transitions = states_module.TRANSITIONS
        all_pairs = {(state, event) for state in states for event in events}
        visited = set()
        walks = 0
        for length in range(0, 5):
            for sequence in itertools.product(events, repeat=length):
                walks += 1
                state = "FLOWING"
                for event in sequence:
                    visited.add((state, event))
                    state = library_module.step(state, event)
                self.assertIn(state, states, f"walk {sequence} left the states")
                via_real = fold_real(sequence)
                self.assertEqual(
                    state,
                    via_real,
                    f"library model and real machine disagree on walk {sequence}",
                )
        self.assertEqual(
            walks,
            sum(len(events) ** k for k in range(5)),
            "the enumeration must cover every walk up to length 4",
        )
        self.assertEqual(visited, all_pairs, "the walk must visit every (state, event) pair")

    def test_traffic_rows_interleaved_do_not_move_the_machine(self):
        """N1: traffic rows interleaved between events move nothing.

        The exhaustive walk again, with a traffic row between every
        event and at both ends. The fold must not change. The traffic
        row here is synthetic; a corpus drawn from a real journal's
        SELECT DISTINCT outcome, status, reason is a [RUN] item.
        """
        events = sorted({event for (_source, event) in states_module.TRANSITIONS})
        for length in range(0, 5):
            for sequence in itertools.product(events, repeat=length):
                rows = [dict(TRAFFIC_ROW)]
                for event in sequence:
                    rows.append(dict(ROW_FOR_EVENT[event]))
                    rows.append(dict(TRAFFIC_ROW))
                self.assertEqual(
                    states_module.run_state(rows),
                    fold_library(sequence),
                    f"traffic rows moved the machine on walk {sequence}",
                )

    def test_row_shapes(self):
        """H10: _event_for recognizes the row shapes, including the None ones."""
        for row, expected in ROW_SHAPES:
            with self.subTest(row=row):
                self.assertEqual(states_module._event_for(dict(row)), expected)

    def test_random_row_soup_never_leaves_the_states(self):
        """N5: a fixed-seed soup of row shapes never breaks run_state.

        5,000 sequences drawn from a corpus that includes None
        reasons, unicode, "cap block", and "tunnel cut by" rows.
        run_state must never raise or leave the states, and appending
        a traffic row must change nothing. Deterministic: the seed is
        fixed, so the soup is the same every run. No hypothesis needed.
        """
        rng = random.Random(2026)
        corpus = [dict(row) for row, _ in ROW_SHAPES] + [dict(TRAFFIC_ROW)]
        corpus.append({"outcome": "paused", "status": "unknown", "reason": "caf\u00e9 \u2603"})
        corpus.append({"outcome": "allowed", "status": "success", "reason": ""})
        for i in range(5000):
            length = rng.randrange(0, 7)
            rows = [dict(rng.choice(corpus)) for _ in range(length)]
            state = states_module.run_state(rows)
            self.assertIn(state, states_module.STATES, f"soup sequence {i} left the states")
            self.assertEqual(
                states_module.run_state(rows + [dict(TRAFFIC_ROW)]),
                state,
                f"a trailing traffic row moved the machine on soup sequence {i}",
            )

    def test_every_state_can_return_to_flowing(self):
        """The machine never traps a Run: every state can flow again.

        Labelled invariant (D6): the spec is silent, but no state is
        terminal, which a future hard-stop state would break.
        """
        for state in library_module.STATES:
            reached = reachable_from(state, library_module.TRANSITIONS)
            self.assertIn("FLOWING", reached, f"{state} can never return to FLOWING")

    def test_unknown_event_leaves_state_unchanged(self):
        """An event the machine does not know moves nothing."""
        for state in library_module.STATES:
            self.assertEqual(library_module.step(state, "no-such-event"), state)

    def test_step_raises_on_invalid_state(self):
        """N4 (D7): step on a state that does not exist raises, it does not pass through."""
        with self.assertRaises(ValueError):
            library_module.step("NOT_A_STATE", "pause")
        with self.assertRaises(ValueError):
            library_module.step("", "pause")

    def test_traffic_rows_do_not_move_the_machine(self):
        """Only control rows move a Run; traffic rows are not events."""
        self.assertEqual(states_module.run_state([dict(TRAFFIC_ROW)]), "FLOWING")
        rows = [dict(TRAFFIC_ROW), dict(ROW_FOR_EVENT["pause"]), dict(TRAFFIC_ROW)]
        self.assertEqual(states_module.run_state(rows), "PAUSED")


class RealJournalRowShapesTest(unittest.TestCase):
    """H10: the row shapes _event_for recognizes are the rows the proxy really journals.

    The pure tests above use synthetic rows. These tests produce rows
    through the proxy's real API on a fresh trace and assert
    _event_for reads them as the right event. Cap rows are the
    exception: producing one needs a spend-cap rule and spend, so
    cap_block and cap_warn are covered by the ROW_SHAPES table, not
    here. No network: the state is built directly, no server socket.
    """

    DONE = {
        "task_type": "local file write",
        "clause": "row exists after close/fsync",
        "read_back": "named read-back: file row exists after close/fsync",
        "approved_by": "human",
    }

    @classmethod
    def setUpClass(cls):
        """One proxy state on a temp db. No server socket, no echo."""
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "attempts.db")
        cls.state = proxy_module.ProxyState(cls.db_path)
        cls.dest = "127.0.0.1:9999"

    @classmethod
    def tearDownClass(cls):
        """Close the connection and the temp db. No files left behind."""
        cls.state.conn.close()
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

    def write_file(self, name, text):
        """Write one file, synced, LF endings on every platform."""
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8", newline="\n") as proof:
            proof.write(text)
            proof.flush()
            os.fsync(proof.fileno())
        return path

    def test_pause_row(self):
        """A pause_run journals a row _event_for reads as pause."""
        trace = "real-pause"
        self.assertTrue(self.state.pause_run(trace))
        rows = self.rows_for(trace)
        row = next(r for r in rows if r["outcome"] == "paused")
        self.assertEqual(row["reason"], "human pause")
        self.assertEqual(states_module._event_for(row), "pause")

    def test_redirect_row(self):
        """A redirect_run journals a row _event_for reads as redirect."""
        trace = "real-redirect"
        self.assertTrue(self.state.redirect_run(trace, self.dest))
        rows = self.rows_for(trace)
        row = next(r for r in rows if r["outcome"] == "redirected")
        self.assertEqual(row["reason"], "human redirect")
        self.assertEqual(states_module._event_for(row), "redirect")

    def test_stop_row_is_red_button(self):
        """A stop_run journals a row _event_for reads as red_button."""
        trace = "real-stop"
        self.assertTrue(self.state.stop_run(trace))
        rows = self.rows_for(trace)
        row = next(r for r in rows if r["outcome"] == "stopped")
        self.assertEqual(row["reason"], "human stop")
        self.assertEqual(states_module._event_for(row), "red_button")

    def test_verify_ok_row_from_verify_read_back(self):
        """A Correct Verify journals a verdict row _event_for reads as verify_ok, and a call row it reads as nothing."""
        trace = "real-verify-ok"
        text = "2026-11-28T08:00:00Z INFO ok\n"
        path = self.write_file("real-ok.log", text)
        claim = {"kind": "local-file", "path": path, "expected_text": text, "idempotency_key": "real-ok-1"}
        verdict, _ = self.state.verify_read_back(trace, claim, done_clause=self.DONE)
        self.assertEqual(verdict, "Correct")
        rows = self.rows_for(trace)
        call = next(r for r in rows if r["reason"] == "verify call real-ok-1")
        self.assertIsNone(states_module._event_for(call), "the call row is Verify talking about itself")
        verdict_row = next(r for r in rows if r["reason"] == "verify Correct")
        self.assertEqual(verdict_row["status"], "success")
        self.assertEqual(states_module._event_for(verdict_row), "verify_ok")

    def test_verify_ok_row_from_verify_run(self):
        """A verify_run after a pause journals a row _event_for reads as verify_ok."""
        trace = "real-verify-run"
        self.assertTrue(self.state.pause_run(trace))
        self.assertTrue(self.state.verify_run(trace, done_clause=self.DONE))
        rows = self.rows_for(trace)
        row = next(r for r in rows if r["outcome"] == "" and r["reason"].startswith("verify ok"))
        self.assertEqual(states_module._event_for(row), "verify_ok")

    def test_resume_row_from_retry_run(self):
        """A retry_run after a fresh Verify journals a row _event_for reads as resume."""
        trace = "real-resume"
        self.assertTrue(self.state.pause_run(trace))
        self.assertTrue(self.state.verify_run(trace, done_clause=self.DONE))
        allowed, reason = self.state.retry_run(trace)
        self.assertTrue(allowed, reason)
        rows = self.rows_for(trace)
        row = next(r for r in rows if r["reason"] == "retry allowed after verify")
        self.assertEqual(row["outcome"], "")
        self.assertEqual(states_module._event_for(row), "resume")


if __name__ == "__main__":
    unittest.main()
