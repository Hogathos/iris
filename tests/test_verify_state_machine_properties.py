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
      TRANSITIONS.get((state, event), state) rule).
The library's model must match the real machine exactly: a model that
omits a transition, or adds an invalid one, fails here. That is the
point -- a test that proves the implementation wrong.

Deterministic exhaustive enumeration, no randomness. Hypothesis is a
CI test dependency (the workflow installs it, and
test_bench_properties.py uses it), but it is not installed in this
environment, and for a finite explicit transition table enumeration
is a complete proof, stronger than sampling: every event sequence up
to length 4 is folded, and reachability is proved by breadth-first
search over the whole graph, not by sampling walks.

Charles's framing: these tests are based on the evidence outlined and
as verifiable as they can be. If something breaks, that is a discovery,
not a defect.

No network, no proxy, no database. Pure functions of the machine.
"""

import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
    frontier = [start]
    while frontier:
        state = frontier.pop()
        for (source, _event), target in transitions.items():
            if source == state and target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


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

    def test_no_invalid_state_on_exhaustive_walks(self):
        """Criterion 5: every walk of the machine stays inside the states.

        All event sequences up to length 4, folded twice: once through
        the library's model, once through the real machine over
        synthetic journal rows. The two folds must agree, and neither
        may ever leave the states.
        """
        states = library_module.STATES
        events = sorted({event for (_source, event) in library_module.TRANSITIONS})
        walks = 0
        for length in range(0, 5):
            for sequence in itertools.product(events, repeat=length):
                walks += 1
                via_library = fold_library(sequence)
                self.assertIn(via_library, states, f"walk {sequence} left the states")
                via_real = fold_real(sequence)
                self.assertEqual(
                    via_library,
                    via_real,
                    f"library model and real machine disagree on walk {sequence}",
                )
        self.assertGreater(walks, 1000, "the enumeration must cover thousands of walks")

    def test_every_state_can_return_to_flowing(self):
        """The machine never traps a Run: every state can flow again."""
        for state in library_module.STATES:
            reached = reachable_from(state, library_module.TRANSITIONS)
            self.assertIn("FLOWING", reached, f"{state} can never return to FLOWING")

    def test_unknown_event_leaves_state_unchanged(self):
        """An event the machine does not know moves nothing."""
        for state in library_module.STATES:
            self.assertEqual(library_module.step(state, "no-such-event"), state)

    def test_traffic_rows_do_not_move_the_machine(self):
        """Only control rows move a Run; traffic rows are not events."""
        self.assertEqual(states_module.run_state([dict(TRAFFIC_ROW)]), "FLOWING")
        rows = [dict(TRAFFIC_ROW), dict(ROW_FOR_EVENT["pause"]), dict(TRAFFIC_ROW)]
        self.assertEqual(states_module.run_state(rows), "PAUSED")


if __name__ == "__main__":
    unittest.main()
