"""Swarm map node click + color test (test-first, failing until built).

Problem in one sentence: a Run node is a hollow 8-pixel circle, so
only its 1-pixel outline is clickable, and it carries no color, so
the map does not read at a glance.

Done clause for this test Run (human-approved, once per task type, as
a named read-back, before the Run starts):
  task_type = "local file write"
  clause    = "row exists after close/fsync"
  read_back = "named read-back: file row exists after close/fsync"
No approved done = Unverified, never Correct.

Expected failure right now: build_map gives a Run node no color, and
to_svg draws its circle with fill="none", so the disc is dead space
and the node carries no outcome color. That failure is correct. It
is the test-first proof. Charles reviews this test with the finished
work, because he chose the build ("We can do your idea, I do like
it").

Loopback only. Metadata only in journal rows. No bodies, no keys,
no Authorization headers, no real hostnames.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

from devpanel import map as map_module


def _row(trace_id, dest, outcome="", status="success", latency=0.1, created_at="2026-11-22T06:00:00+00:00", reason=""):
    """One hand-built journal row, in the shape the proxy writes."""
    return {
        "trace_id": trace_id,
        "dest_host": dest,
        "endpoint": dest,
        "method": "GET",
        "outcome": outcome,
        "status": status,
        "latency_seconds": latency,
        "created_at": created_at,
        "reason": reason,
        "bytes_in": 0,
        "bytes_out": 0,
    }


def _node(built, kind, label):
    """One node out of built map data, by kind and label."""
    for node in built["nodes"]:
        if node["kind"] == kind and node["label"] == label:
            return node
    raise AssertionError(f"no {kind} node labeled {label!r}")


class SwarmMapNodeColorTest(unittest.TestCase):
    """A Run node's color is its worst outcome, like its edge."""

    def test_allowed_run_is_green(self):
        """A run with only allowed rows is green."""
        built = map_module.build_map([{"trace_id": "run-green", "rows": [_row("run-green", "127.0.0.1:18080")]}])
        self.assertEqual(_node(built, "run", "Run run-gree")["color"], "green")

    def test_rate_limited_run_is_amber(self):
        """A run with a rate-limited row is amber."""
        built = map_module.build_map(
            [
                {
                    "trace_id": "run-amber",
                    "rows": [
                        _row("run-amber", "127.0.0.1:18080", outcome="rate_limited", reason="slowed down by the rate limit.")
                    ],
                }
            ]
        )
        self.assertEqual(_node(built, "run", "Run run-ambe")["color"], "amber")

    def test_blocked_run_is_red(self):
        """A run with a blocked row is red."""
        built = map_module.build_map(
            [{"trace_id": "run-red", "rows": [_row("run-red", "example.com", outcome="blocked", reason="not allowlisted")]}]
        )
        self.assertEqual(_node(built, "run", "Run run-red")["color"], "red")

    def test_mixed_run_takes_worst_color(self):
        """Allowed plus rate-limited rows in one run is amber."""
        built = map_module.build_map(
            [
                {
                    "trace_id": "run-mixed",
                    "rows": [
                        _row("run-mixed", "127.0.0.1:18080"),
                        _row(
                            "run-mixed",
                            "127.0.0.1:18080",
                            outcome="rate_limited",
                            reason="slowed down by the rate limit.",
                            created_at="2026-11-22T06:00:01+00:00",
                        ),
                    ],
                }
            ]
        )
        self.assertEqual(_node(built, "run", "Run run-mixe")["color"], "amber")


class SwarmMapNodeClickTest(unittest.TestCase):
    """A Run node's whole disc is clickable, not just its outline."""

    def test_run_circle_is_filled(self):
        """The rendered circle carries a fill, never fill=none."""
        built = map_module.build_map([{"trace_id": "run-fill", "rows": [_row("run-fill", "127.0.0.1:18080")]}])
        svg = map_module.to_svg(built)
        start = svg.index("<circle ")
        circle = svg[start : svg.index("/>", start)]
        self.assertNotIn('fill="none"', circle)
        self.assertIn('fill="green"', circle)

    def test_run_circle_fill_matches_node_color(self):
        """The circle's fill is the node's worst-outcome color."""
        built = map_module.build_map(
            [
                {
                    "trace_id": "run-amber",
                    "rows": [
                        _row("run-amber", "127.0.0.1:18080", outcome="rate_limited", reason="slowed down by the rate limit.")
                    ],
                }
            ]
        )
        svg = map_module.to_svg(built)
        self.assertIn('fill="amber"', svg)

    def test_dest_square_still_renders(self):
        """The destination square keeps its shape, filled white."""
        built = map_module.build_map([{"trace_id": "run-dest", "rows": [_row("run-dest", "127.0.0.1:18080")]}])
        svg = map_module.to_svg(built)
        self.assertIn("<rect ", svg)
        self.assertIn("127.0.0.1:18080", svg)
        start = svg.index("<rect ")
        rect = svg[start : svg.index("/>", start)]
        self.assertIn('fill="white"', rect)


if __name__ == "__main__":
    unittest.main()
