"""Devtool panel seam: human-only actions for the dev-only panel.

This module is dev only, never shipped in the consumer app. It holds
plain Python functions a human clicks in the local dev panel. There is
no HTTP route here and no remote caller: the panel button calls these
functions in the same local process that runs the watched proxy.

Every action reuses the proxy's existing machinery and reason
vocabulary. A new finger on the same human-only hand: no new gate,
no new outcome, no new verdict.
"""

from __future__ import annotations


def pause_run(state, trace_id: str, reason: str = "human pause") -> bool:
    """Pause one Run when the human presses the panel Pause button.

    Human only, local only. Delegates to the proxy's own pause, so the
    journal row comes first and the live tunnel is cut right after,
    exactly like a direct pause. Returns True when the Run is paused
    (or already was), False when the human gave no usable reason.
    """
    return state.pause_run(trace_id, reason=reason)


def verify_run(state, trace_id: str, read_back: str = "") -> bool:
    """Verify one Run when the human presses the panel Verify button.

    Human only, local only. The typed words become the human-approved
    done clause: approved by human, read back is what the human typed.
    Delegates to the proxy's own Verify gate, so an empty read-back or
    a Run that is neither paused nor redirected is refused with no row.
    Returns True when the gate is stamped, False when it refuses.
    """
    words = (read_back or "").strip()
    if not words:
        return False
    done_clause = {"approved_by": "human", "read_back": words}
    return state.verify_run(trace_id, done_clause)
