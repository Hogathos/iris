"""The Iris demo: one Run that really saves notes and reads them back.

The human presses Run the demo. The demo agent saves real notes to a
notebook file on this computer, then Iris reads the file back for
each claim, through the same Verify read-back the human gate uses.
No staged verdicts: a Correct means the bytes matched, an Incorrect
means they contradicted the claim, an Unverified means Iris could
not look. The third claim is Incorrect, so Iris stops the Run.

Loopback only. The notebook is a plain JSON file. Nothing leaves
this computer.
"""
from __future__ import annotations

import json
import time
import uuid

import db as db_module

#: The notebook holds two real notes when the demo ends.
NOTE_ONE = "groceries"
NOTE_TWO = "bread"

#: What the demo agent says, in order. The words are pinned by the
#: spec: the panel shows them under "What the agent said".
CLAIM_ONE_SAID = 'I saved a note called "groceries".'
CLAIM_TWO_SAID = "I checked the weather service."
CLAIM_THREE_SAID = "I saved 3 notes in total."

#: What Iris found for each claim. Claim one and three come from a
#: real file read; claim three counts real notes. Claim two names
#: the reason Iris could not check: the service is off this computer.
FOUND_ONE = ('Iris looked in the demo notebook. The note "groceries" '
             "is there.")
FOUND_TWO = ("That service is on another computer. Iris only looks "
             "at this computer, so it could not check.")
FOUND_THREE = ("Iris looked in the demo notebook. It holds 2 notes, "
               "not 3.")

LOOKED_AT = "the demo notebook"

#: The human word behind the demo checks: the human pressed the button.
DEMO_READ_BACK = "demo: the human pressed Run the demo"

#: Journal marker: every demo Verify call row carries this prefix, so
#: the panel can mark the Run Demo without a second store.
DEMO_KEY_PREFIX = "demo"


def _write_notes(path, notes) -> None:
    """Save notes to the notebook file. A real write, flushed shut."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(notes, handle, indent=2)
        handle.flush()


def _read_notes(path):
    """Read the notebook back. A real read after the write."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def run_demo(state, notebook_path: str,
             interval_seconds: float = 2.0) -> str:
    """Run the demo against live proxy state. Returns the trace id.

    Three claims, about interval_seconds apart, each checked by a
    real Verify read-back and journaled as a claim row. The third
    claim is Incorrect, so the Run stops. Nothing is staged: change
    the notebook and the verdicts change with it.
    """
    import datetime

    trace_id = uuid.uuid4().hex
    done = {"approved_by": "human", "read_back": DEMO_READ_BACK}

    def check(said, claim, found, looked_at):
        start = time.perf_counter()
        verdict, _card = state.verify_read_back(
            trace_id, claim, done_clause=dict(done))
        took_ms = (time.perf_counter() - start) * 1000.0
        now = datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds")
        db_module.save_claim(state.conn, trace_id, said, verdict,
                             looked_at, found, took_ms, now)
        return verdict

    _write_notes(notebook_path, [NOTE_ONE])
    key_one = f"{DEMO_KEY_PREFIX} claim 1"
    check(CLAIM_ONE_SAID,
          {"idempotency_key": key_one, "kind": "local-file",
           "path": notebook_path,
           "expected_text": json.dumps([NOTE_ONE], indent=2)},
          FOUND_ONE, LOOKED_AT)
    time.sleep(interval_seconds)

    key_two = f"{DEMO_KEY_PREFIX} claim 2"
    check(CLAIM_TWO_SAID,
          {"idempotency_key": key_two, "kind": "http",
           "url": "https://weather.example/"},
          FOUND_TWO, "that service")
    time.sleep(interval_seconds)

    _write_notes(notebook_path, [NOTE_ONE, NOTE_TWO])
    actual = _read_notes(notebook_path)
    key_three = f"{DEMO_KEY_PREFIX} claim 3"
    verdict = check(
        CLAIM_THREE_SAID,
        {"idempotency_key": key_three, "kind": "local-file",
         "path": notebook_path,
         "expected_text": json.dumps(
             [NOTE_ONE, NOTE_TWO, "milk"], indent=2)},
        FOUND_THREE.replace("2 notes",
                            f"{len(actual)} notes"),
        LOOKED_AT)
    if verdict == "Incorrect":
        state.stop_run(trace_id, reason="incorrect claim")
    return trace_id
