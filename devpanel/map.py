"""Swarm map, 2D: a pure read of the journal as node places.

Dev only, loopback only. Server-rendered SVG, zero JavaScript: the
server builds the picture, links and forms only, the same as the
panel. A read of the journal, nothing more. It collects no new data.

Nodes are Runs, not named agents: the journal carries no agent
identity, so the map never invents a name for one. Time runs left to
right. Each node keeps its own lane, so no two nodes share a place.
Same journal, same places, every time.

Metadata only in the rows it reads. No bodies, no keys, no
Authorization headers, no real hostnames beyond the loopback
destination label already on the Run's own rows.
"""
from __future__ import annotations

import datetime
import html
from urllib.parse import quote

# Methods a Steer control action writes, not a call. A journal row
# with one of these is a control row (VERIFY, RETRY, APPROVE, CAP,
# TIMEOUT) or a close row (empty method), never a call to a
# destination. Only real HTTP methods count as calls.
CONTROL_METHODS = frozenset(
    {"", "VERIFY", "RETRY", "APPROVE", "CAP", "TIMEOUT"})

MAX_RUNS = 20
WIDTH = 900
LEFT = 80
RIGHT = 80
TOP = 40
BOTTOM = 20
LANE_HEIGHT = 44


def _parse_time(text: str) -> datetime.datetime:
    """An ISO journal time as a datetime, or the earliest time."""
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return datetime.datetime.min.replace(
            tzinfo=datetime.timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _seconds_text(total: float) -> str:
    """A summed latency in seconds, without trailing zeros."""
    text = f"{total:.3f}".rstrip("0").rstrip(".")
    return f"{text}s"


def _row_color(row: dict) -> str:
    """One row's edge color: green allowed, red blocked or failed,
    amber rate-limited or retry."""
    outcome = row.get("outcome") or ""
    if outcome in ("rate_limited", "retry"):
        return "amber"
    if outcome == "blocked" or (row.get("status") or "") == "failure":
        return "red"
    return "green"


def _short(trace_id: str) -> str:
    """The short Run name the run list uses."""
    return "Run " + trace_id[:8]


def build_map(traces: list) -> dict:
    """Build the map data from journal traces, newest 20 Runs only.

    Traces is a list of {"trace_id": str, "rows": [row dict]}. Rows
    carry trace_id, dest_host, outcome, status, latency_seconds,
    created_at, and reason. Returns {"nodes": [...], "edges": [...],
    "width": int, "height": int}. Node ids are "run:{trace_id}" and
    "dest:{dest}". Pure function: same journal, same places.
    """
    timed = []
    for trace in traces:
        rows = trace.get("rows") or []
        if not rows:
            continue
        first = min(_parse_time(r.get("created_at") or "")
                    for r in rows)
        timed.append((first, trace["trace_id"], rows))
    timed.sort(key=lambda item: (item[0], item[1]))
    timed = timed[-MAX_RUNS:]

    if timed:
        earliest = timed[0][0]
        latest = max(first for first, _tid, _rows in timed)
    else:
        earliest = latest = _parse_time("")
    span = (latest - earliest).total_seconds()

    def x_for(when: datetime.datetime) -> int:
        """A time mapped left to right across the picture."""
        if span <= 0:
            return LEFT
        fraction = ((when - earliest).total_seconds() / span)
        return LEFT + round(fraction * (WIDTH - LEFT - RIGHT))

    nodes = []
    edges = []
    run_xy = {}
    dest_xy = {}
    dest_first = {}
    lane = 0
    for first, trace_id, rows in timed:
        node_id = f"run:{trace_id}"
        node = {
            "id": node_id, "kind": "run",
            "label": _short(trace_id),
            "href": f"/runs/{trace_id}",
            "x": x_for(first), "y": TOP + lane * LANE_HEIGHT,
        }
        lane += 1
        nodes.append(node)
        run_xy[node_id] = (node["x"], node["y"])
        for row in rows:
            dest = row.get("dest_host") or ""
            if not dest:
                continue
            when = _parse_time(row.get("created_at") or "")
            if dest not in dest_first or when < dest_first[dest]:
                dest_first[dest] = when
    for dest in sorted(dest_first, key=lambda d: (dest_first[d], d)):
        node_id = f"dest:{dest}"
        node = {
            "id": node_id, "kind": "dest",
            "label": dest,
            "href": "/?dest=" + quote(dest),
            "x": x_for(dest_first[dest]),
            "y": TOP + lane * LANE_HEIGHT,
        }
        lane += 1
        nodes.append(node)
        dest_xy[node_id] = (node["x"], node["y"])
    for _first, trace_id, rows in timed:
        calls: dict[str, list] = {}
        for row in rows:
            if (row.get("method") or "") in CONTROL_METHODS:
                continue
            dest = row.get("dest_host") or ""
            if not dest:
                continue
            calls.setdefault(dest, []).append(row)
        for dest in sorted(calls):
            call_rows = calls[dest]
            colors = {_row_color(r) for r in call_rows}
            if "red" in colors:
                color = "red"
            elif "amber" in colors:
                color = "amber"
            else:
                color = "green"
            total = sum(float(r.get("latency_seconds") or 0)
                        for r in call_rows)
            from_x, from_y = run_xy[f"run:{trace_id}"]
            to_x, to_y = dest_xy[f"dest:{dest}"]
            edges.append({
                "from": f"run:{trace_id}", "to": f"dest:{dest}",
                "color": color,
                "label": (f"{len(call_rows)} calls, "
                          f"{_seconds_text(total)}"),
                "from_x": from_x, "from_y": from_y,
                "to_x": to_x, "to_y": to_y,
            })
    height = TOP + lane * LANE_HEIGHT + BOTTOM
    return {"nodes": nodes, "edges": edges,
            "width": WIDTH, "height": height}


def to_svg(built: dict) -> str:
    """Render built map data as one inline SVG, zero JavaScript."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{built["width"]}" height="{built["height"]}" '
        f'role="img" aria-label="Swarm map">']
    for edge in built["edges"]:
        mid_x = (edge["from_x"] + edge["to_x"]) // 2
        mid_y = (edge["from_y"] + edge["to_y"]) // 2
        parts.append(
            f'<line x1="{edge["from_x"]}" y1="{edge["from_y"]}" '
            f'x2="{edge["to_x"]}" y2="{edge["to_y"]}" '
            f'stroke="{edge["color"]}" stroke-width="2" />')
        parts.append(
            f'<text x="{mid_x}" y="{mid_y - 6}">'
            f'{html.escape(edge["label"])}</text>')
    for node in built["nodes"]:
        href = html.escape(node["href"], quote=True)
        label = html.escape(node["label"])
        if node["kind"] == "run":
            shape = (f'<circle cx="{node["x"]}" cy="{node["y"]}" '
                     f'r="8" fill="none" stroke="black" />')
        else:
            shape = (f'<rect x="{node["x"] - 8}" y="{node["y"] - 8}" '
                     f'width="16" height="16" fill="none" '
                     f'stroke="black" />')
        parts.append(
            f'<a href="{href}">{shape}'
            f'<text x="{node["x"] + 12}" y="{node["y"] + 4}">'
            f'{label}</text></a>')
    parts.append("</svg>")
    return "".join(parts)
