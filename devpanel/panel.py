"""Iris panel: the Runs, Limits, and Tools pages.

One server renders two faces. Inside the consumer app it is the
dashboard behind the native shell, at ?tier=simple (or ?tier=advanced
with More detail on). Standalone it is the Developer panel, with the
extra pages. Both faces post to the same server on this computer
only, and every post carries a per-launch token the server checks
alongside the Host header.

Plain words only on screen: no trace_id without "Run", no stat
without its meaning. Verdicts read Correct, Incorrect, Unverified,
never failed, never unknown. Costs show only when a real source gave
them: Iris never guesses.
"""
from __future__ import annotations

import argparse
import datetime
import html
import os
import secrets
import sqlite3
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import api as api_module
import db as db_module
import proxy as proxy_module
from devpanel import map as map_module
from devpanel import pause_run as seam_pause_run
from devpanel import states as states_module
from devpanel import verify_run as seam_verify_run
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

#: The loopback spot number: the address the human gives an agent.
#: It is the proxy port. 8888.
SPOT = 8888

#: Consumer build: the app sets this True at startup, so the panel
#: serves no Developer pages or routes at all. Hiding them with CSS
#: is not enough: the routes answer 404. The dev server leaves this
#: False.
CONSUMER_BUILD = False

#: Per-launch token, in memory only. Made fresh at every startup,
#: never written to disk, never logged, never shown on screen. Every
#: post the panel makes carries it; a post without it is refused.
_server_token: str | None = None
#: The panel's own port, recorded at startup for the Host check.
_server_port: int | None = None


def set_consumer_build(on: bool) -> None:
    """Serve (or stop serving) the Developer pages and routes."""
    global CONSUMER_BUILD
    CONSUMER_BUILD = bool(on)


def new_panel_token() -> str:
    """A fresh per-launch token. Secrets only, never stored anywhere."""
    return secrets.token_hex(32)


def install_identity(port: int) -> str:
    """Record this launch: fresh token plus the panel's own port."""
    global _server_token, _server_port
    _server_token = new_panel_token()
    _server_port = int(port)
    return _server_token


def clear_identity() -> None:
    """Forget this launch. Tests and shutdown use this, nothing else."""
    global _server_token, _server_port
    _server_token = None
    _server_port = None


def server_token() -> str | None:
    """The current per-launch token, or None before startup."""
    return _server_token


def check_host(host_header: str | None) -> bool:
    """True when the Host header names this computer and panel port.

    Only 127.0.0.1 and localhost with the panel's own port pass. When
    no server identity is installed (tests calling routes directly),
    the check stays out of the way.
    """
    if _server_port is None:
        return True
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        return False
    name, sep, port = host.rpartition(":")
    if not sep or not port.isdigit():
        return False
    return name in ("127.0.0.1", "localhost") and int(port) == _server_port


def check_token(token: str | None) -> bool:
    """True when the post carries this launch's token.

    When no server identity is installed (tests calling routes
    directly), the check stays out of the way.
    """
    if _server_token is None:
        return True
    return bool(token) and secrets.compare_digest(token, _server_token)


def _host_of(request) -> str | None:
    """The Host header of a request, or None when it has none."""
    headers = getattr(request, "headers", None) or {}
    if hasattr(headers, "get"):
        return headers.get("host")
    return None


def _refused_request(request, token: str = ""):
    """Refuse a request that fails the Host or token check, or None.

    The Host check runs on every request. The token check runs on
    every post. A refused post changes nothing and says so in plain
    words. A refused Host is a 403 in plain words.
    """
    if not check_host(_host_of(request)):
        return HTMLResponse(
            "<html><body><p>Iris refused this request because it did "
            "not come from this computer.</p></body></html>",
            status_code=403)
    if not check_token(token):
        return HTMLResponse(
            "<html><body><p>This page is out of date. Reload it.</p>"
            "</body></html>",
            status_code=403)
    return None


def _gone() -> HTMLResponse:
    """A Developer route in the consumer build: nothing here."""
    return HTMLResponse(
        "<html><body><p>Not found in this build.</p></body></html>",
        status_code=404)


def _dev_only(request, tier: str = ""):
    """404 for Developer routes in the consumer build."""
    if CONSUMER_BUILD:
        return _gone()
    return None


def _tier_gone(tier: str = ""):
    """404 for the developer tier in the consumer build."""
    if CONSUMER_BUILD and tier == "developer":
        return _gone()
    return None


def _version() -> str:
    """The Iris version for the footer, from the VERSION file."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "VERSION"),
                  encoding="utf-8") as handle:
            return handle.read().strip() or "0.1.0"
    except OSError:
        return "0.1.0"


# Shared strings: the app shell uses these same objects, so the two
# faces can never drift apart where the same thing appears.
VERIFY_TOOLTIP = ("Check the selected Run. You type what was checked, "
                  "in plain words, and stamp approval.")
PAUSE_TOOLTIP = ("Hold the selected Run. Its next call waits until you "
                 "click Resume.")
RESUME_TOOLTIP = "Let the selected Run go on."
EMERGENCY_TOOLTIP = ("Block every call from every agent that uses Iris. "
                     "Nothing gets through until you turn it back on.")
MORE_TOOLTIP = ("Shows how Iris reached each verdict, and a few extra "
                "controls.")
WATCHING_ON_TOOLTIP = ("Iris is watching your agents. Click to turn "
                       "watching off.")
WATCHING_OFF_TOOLTIP = "Watching is off. Click to turn it on."
RUN_TOOLTIP = "One job your agent does, from start to end."
STARTED_TOOLTIP = "When the Run's first call reached Iris."
STATUS_TOOLTIP = "Where the Run is now."
VERDICT_TOOLTIP = ("Whether the agent's claims check out. Correct, "
                   "Incorrect, or Unverified. Unverified means Iris "
                   "could not tell. Iris never guesses.")
COST_TOOLTIP = ("What this Run cost, when Iris can work it out. "
                "Otherwise it says Unverified.")
CALLS_TOOLTIP = "How many times the agent reached out during this Run."
WENT_TO_TOOLTIP = "The places the calls went."
PICK_FIRST_TOOLTIP = "Pick a Run in the list first."
PICK_FIRST_APP_TOOLTIP = "Pick a Run in the table first."

SUMMARY_NOTHING = ("The agent is working. Iris has not checked any "
                   "claims yet.")
SUMMARY_SO_FAR = "The agent is working. Every claim so far is Correct."
SUMMARY_WORKING = "The agent is working."
SUMMARY_COULD_NOT = ("The agent is working. Iris could not check every "
                     "claim.")
SUMMARY_PAUSED = "You paused this Run. Its next call is waiting."
SUMMARY_STOPPED = "Iris stopped this Run. A claim was Incorrect."
SUMMARY_ESTOP = "Emergency stop ended this Run."
SUMMARY_FINISHED = "The agent finished. Every claim is Correct."
SUMMARY_FINISHED_UNVERIFIED = ("The agent finished. Iris could not check "
                               "every claim.")

STRIP_ESTOP = ("Emergency stop is on. Iris is blocking every call. Your "
               "agent may still be running. Close it yourself if you want "
               "it fully off.")
STRIP_WAITING = ("Run {prefix} is waiting for you. The agent says it "
                 "finished, and Iris could not confirm it.")
STRIP_BLOCKED = ("Iris blocked a call to {place}. It could not work out "
                 "what the call costs, and you have a daily limit set.")
STRIP_ALLOWED = ("{place} is allowed. Calls to it will not count toward "
                 "your limit.")
STRIP_PORT = ("Iris couldn't start watching. Another program on this "
              "computer is using the spot Iris needs.")

FIRST_RUN_ONE = "Iris watches what your agent does on this computer."
FIRST_RUN_TWO = "Everything stays on this computer. Iris needs no account."
FIRST_RUN_THREE = ("It sees where each tool call goes, how big it is, "
                   "and how long it takes.")
FIRST_RUN_FOUR = ("When your agent says it did something, Iris looks for "
                  "itself and tells you: Correct, Incorrect, or Unverified. "
                  "Unverified means Iris could not tell. Iris will tell "
                  "you if an action is unverifiable, and ask you what "
                  "to do.")
FIRST_RUN_DEMO_LINE = "The demo runs on this computer only. It costs $0.00."
FIRST_RUN_DEMO = "Run the demo"
FIRST_RUN_SKIP = "Skip the demo and open the dashboard"

LOADING_STARTING = "Starting Iris…"
LOADING_SLOW = "Iris is taking longer than usual."
LOADING_RETRY = "Try again"
LOADING_FAILED = ("The dashboard didn't load. Verify, Pause, and "
                  "Emergency stop still work.")

SAVED_WORD = "Saved."
SAVE_NOTE = ("The file lists what each Run did and what Iris found. "
             "It holds no message text and no keys.")

SETTINGS_UPDATES = ("Iris never updates itself. Use Help, then Check for "
                    "updates, to see if there is a newer version.")
SETTINGS_JOURNAL_LINE = ("Everything Iris saves is in this folder. Nothing "
                         "is sent anywhere.")
SETTINGS_SPOT_LINE = ("Your agent needs the new address after you change "
                      "this.")

LIMITS_WARN_LINE = ("Iris warns you at 80% of your daily limit and blocks "
                    "calls at 100%.")
LIMITS_COUNT_LINE = ("Iris counts a cost only when it can work it out. A "
                     "call with an Unverified cost is not counted, unless "
                     "you allow that place below. When a limit is set, Iris "
                     "blocks calls it cannot price, unless you have allowed "
                     "that place.")

DEVELOPER_LINE = ("Developer tools. This page does extra things the Iris "
                  "app does not. It only works on this computer.")
FOOTER_LINE = ("Iris Developer {version}. This page is only on this "
               "computer.")


def _short(trace_id: str) -> str:
    """Human name for a Run: short, plain, no jargon."""
    return "Run " + (trace_id or "?")[:8]


def _esc(text: object) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def _money(value) -> str:
    return "Unverified" if value is None else f"${value:.2f}"


def _tier_query(tier: str) -> str:
    """Query string that keeps the tier, or empty when no tier."""
    return f"?tier={tier}" if tier else ""


def _is_simple(tier: str) -> bool:
    """True only for Simple: hide the diagnostic columns and tabs.

    No tier means show everything, so the dev panel works exactly as
    before. An unknown tier also shows everything, never a blank.
    """
    return tier == "simple"


def _row_words(row: dict) -> str:
    """One plain-words line for a journal row."""
    if row["outcome"] == "paused":
        return "paused: run stopped, retry needs a fresh Verify."
    if row["outcome"] == "blocked":
        return f"blocked: {row['reason'] or 'not allowed'}."
    if row["outcome"] == "rate_limited":
        return "slowed down by the rate limit."
    if row["outcome"] == "redirected":
        return "redirected: run now goes to its safe destination."
    if row["outcome"] == "stopped":
        return "stopped: all runs stopped, approval needed for a new Run."
    if row["outcome"] == "verified":
        if row["status"] == "success":
            return "verified Correct: read back matched the claim."
        if row["status"] == "failure":
            return "verified Incorrect: read back contradicted the claim."
        return "unverified: read back could not be done."
    if row.get("method") == "VERIFY" and row.get(
            "reason", "").startswith("verify ok:"):
        return row["reason"]
    if row["outcome"] == "warned":
        return "warned, money spent at 80% of cap."
    if row["status"] == "success":
        return "allowed: run flowed."
    if row["status"] == "unknown":
        return "unverified: nothing checked this yet."
    return "failed: run did not go through."


def _rows_for(trace_id: str) -> list:
    """Every journal row for one Run, oldest first. Read only."""
    conn = sqlite3.connect(api_module.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM attempts WHERE trace_id = ? ORDER BY id",
            (trace_id,))]
    finally:
        conn.close()


def _claims_for(trace_id: str) -> list:
    """Every checked claim for one Run, oldest first. Read only."""
    conn = sqlite3.connect(api_module.DB_PATH)
    try:
        return db_module.load_claims(conn, trace_id)
    finally:
        conn.close()


def _is_demo(rows) -> bool:
    """True when a journal row marks this Run as the demo."""
    return any((row.get("reason") or "").startswith("verify call demo")
               for row in rows)


def _is_sample(rows) -> bool:
    """True when a journal row marks this Run as made-up sample data."""
    return any((row.get("reason") or "").startswith("verify call sample")
               for row in rows)


def _run_label(trace_id: str, rows) -> str:
    """Run plus prefix, with Demo or Sample where marked."""
    label = _short(trace_id)
    if _is_demo(rows):
        return label + " · Demo"
    if _is_sample(rows):
        return label + " · Sample"
    return label


def _run_verdict(trace_id: str, rows) -> str:
    """The roll-up: claims first, journal Verify rows as fallback."""
    try:
        claims = _claims_for(trace_id)
    except Exception:
        claims = []
    if claims:
        return states_module.claims_rollup(claims)
    return states_module.verdict_for(rows)


def devpanel_verify(state, trace_id: str, read_back: str) -> bool:
    """Stamp the Verify gate with typed words. Human only.

    The same seam the panel Verify route and the app shell call, so
    both faces stamp the same gate with the same words.
    """
    return seam_verify_run(state, trace_id, read_back=read_back)


_STYLE = (
    "<style>"
    "body { font-family: system-ui, sans-serif; font-size: 14px; "
    "margin: 1em auto; max-width: 1100px; line-height: 1.5; "
    "padding: 0 1em; }"
    "table { border-collapse: collapse; margin: 0.5em 0; width: 100%; }"
    "th, td { border-bottom: 1px solid #999; padding: 0.45em 0.6em; "
    "text-align: left; height: 32px; }"
    "thead th { position: sticky; top: 0; background: #fff; }"
    "td.num { text-align: right; font-variant-numeric: tabular-nums; }"
    "button { padding: 0.3em 0.9em; font-size: 14px; }"
    "input, select { padding: 0.2em 0.4em; font-size: 14px; }"
    "section { margin: 1.5em 0; }"
    ".topbar { display: flex; gap: 1em; align-items: center; }"
    ".topbar .status { flex: 1; }"
    ".tabs a { margin-right: 1em; }"
    ".gray { color: #555; }"
    ".alert-amber { border: 1px solid #999; border-left: 6px solid "
    "#b8860b; padding: 0.6em 1em; margin: 1em 0; }"
    ".alert-red { border: 1px solid #999; border-left: 6px solid "
    "#8b0000; padding: 0.6em 1em; margin: 1em 0; }"
    ".danger { border: 2px solid #8b0000; padding: 1em; }"
    "button.estop { background: #8b0000; color: #fff; "
    "border: 2px solid #8b0000; }"
    ".correct { color: #0a6b0a; } .incorrect { color: #8b0000; } "
    ".unverified { color: #8a6d00; }"
    "@media (prefers-color-scheme: dark) { "
    "body { background: #111; color: #eee; } "
    "thead th { background: #111; } "
    ".gray { color: #aaa; } "
    "a { color: #7ab8ff; } }"
    "</style>")


def _token_field() -> str:
    """The hidden per-launch token every post carries."""
    return (f'<input type="hidden" name="token" value="{_esc(_server_token)}">'
            if _server_token else "")


def _started_word(created_at: str) -> str:
    """When the Run started: the time today, else the date and time."""
    text = str(created_at or "")
    try:
        today = datetime.datetime.now(
            datetime.timezone.utc).date().isoformat()
    except Exception:
        today = ""
    if text[:10] == today and len(text) >= 16:
        return text[11:16]
    return text[:16].replace("T", " ")


def _is_loopback(dest: str) -> bool:
    """True for destinations on this computer."""
    host = (dest or "").strip().lower()
    if host.startswith("["):
        return False
    name = host.rpartition(":")[0] or host
    return name in ("127.0.0.1", "localhost", "::1")


def _verdict_cell(verdict: str) -> str:
    """The verdict mark and word. Never color alone."""
    if verdict == "Correct":
        return '<span class="correct">✓ Correct</span>'
    if verdict == "Incorrect":
        return '<span class="incorrect">✕ Incorrect</span>'
    if verdict == "Unverified":
        return '<span class="unverified">? Unverified</span>'
    return '<span class="gray">Not checked yet</span>'


def _outcome_cell(row: dict) -> str:
    """The Outcome word and its tooltip for one call row."""
    outcome = row.get("outcome", "")
    reason = row.get("reason") or ""
    status = row.get("status", "")
    if outcome in ("blocked", "rate_limited"):
        return ('<span title="Iris stopped the call before it went out.">'
                "Blocked</span>")
    if reason.startswith("tunnel cut by"):
        return ('<span title="You or Iris cut the call while it was '
                'in progress.">Cut off</span>')
    if status == "success" and outcome in ("allowed", ""):
        return '<span title="The place answered.">Answered</span>'
    if status == "failure":
        return ('<span title="The place answered with an error, or could '
                'not be reached.">Failed</span>')
    return ('<span title="The call went out, but no answer came back. '
            'Iris can\'t say if it worked.">Unverified</span>')


def _is_call_row(row: dict) -> bool:
    """True for traffic rows: the metadata Iris keeps per call.

    Control rows (pauses, Verify checks, caps) are notes about the
    Run, not calls. The Calls tab shows calls only, never the notes.
    """
    if row.get("method") in ("CAP", "APPROVE", "VERIFY", "RETRY",
                             "TIMEOUT", "OVERRIDE"):
        return False
    return row.get("outcome") in (
        "allowed", "blocked", "rate_limited", "")


CALLS_WORD_TIP = "Each time the agent reached out to something."


def _went_to(call_rows) -> str:
    """Where the calls went, in plain words, with the calls tooltip."""
    dests = []
    for row in call_rows:
        dest = row.get("dest_host") or ""
        if dest and dest not in dests:
            dests.append(dest)
    calls_word = ('<span title="' + _esc(CALLS_WORD_TIP) + '">calls</span>')
    if not dests:
        return ("No calls yet. Iris wrote each claim down before doing "
                "anything else.")
    if all(_is_loopback(dest) for dest in dests):
        n = len(call_rows)
        return (f"{n} {calls_word}. All of them stayed "
                "on this computer.")
    n = len(call_rows)
    m = len(dests)
    return (f"{n} {calls_word} to {m} "
            f"place{'s' if m != 1 else ''}.")


def _went_to_cell(call_rows) -> str:
    """The Went to cell: this computer, or the first place plus more."""
    dests = []
    for row in call_rows:
        dest = row.get("dest_host") or ""
        if dest and dest not in dests:
            dests.append(dest)
    if not dests or all(_is_loopback(dest) for dest in dests):
        return "this computer"
    if len(dests) == 1:
        return _esc(dests[0])
    return _esc(dests[0]) + f" +{len(dests) - 1} more"


def _summary_for(state_word: str, verdict: str, claims,
                 stopped_by_button: bool) -> str:
    """The Run detail summary sentence, one of the pinned set."""
    if state_word == "Paused":
        return SUMMARY_PAUSED
    if state_word == "Stopped":
        if stopped_by_button:
            return SUMMARY_ESTOP
        return SUMMARY_STOPPED
    if state_word == "Finished":
        if verdict == "Correct":
            return SUMMARY_FINISHED
        return SUMMARY_FINISHED_UNVERIFIED
    if not claims:
        return SUMMARY_NOTHING
    if verdict == "Correct":
        return SUMMARY_SO_FAR
    if verdict in ("Incorrect", "Unverified"):
        return SUMMARY_COULD_NOT
    return SUMMARY_WORKING


def _cost_meaning(amount) -> str:
    """The cost meaning line for one Run."""
    if amount is None:
        return "Iris could not work out a cost. It does not guess."
    if amount == 0:
        return "This Run stayed on this computer, so it cost nothing."
    return "Iris worked this out from what it saw."


def _how_got_it(row: dict, cost) -> str:
    """How Iris got one call's cost, in plain words."""
    if cost is None:
        return "Unverified. Iris could not work it out and does not guess."
    if _is_loopback(row.get("dest_host") or ""):
        return "Stayed on this computer, so nothing to pay."
    return "Worked out from the known price for this place."


def _state_of(request):
    """The live proxy state beside the panel, or None watching only."""
    return getattr(getattr(request, "app", None), "state", None) and \
        getattr(request.app.state, "proxy_state", None)


def _selected_trace(request, selected: str = "") -> str:
    return selected or ""


def _collect_runs(show: str = "all", run: str = "", dest: str = "",
                  verdict: str = "", blocked: str = ""):
    """Run summaries, newest first, with state, verdict, and cost."""
    try:
        summaries = api_module.list_traces(limit=1000)["traces"]
    except Exception:
        return []
    runs = []
    for summary in summaries:
        trace_id = summary["trace_id"]
        try:
            rows = _rows_for(trace_id)
        except Exception:
            continue
        if not rows:
            continue
        if run and run not in trace_id:
            continue
        if dest and not any(
                dest in (row.get("dest_host") or "") for row in rows):
            continue
        if blocked and not any(row["outcome"] == "blocked"
                               for row in rows):
            continue
        state = states_module.run_state(rows)
        run_verdict = _run_verdict(trace_id, rows)
        if verdict and verdict != run_verdict:
            continue
        try:
            claims = _claims_for(trace_id)
        except Exception:
            claims = []
        call_rows = [row for row in rows if _is_call_row(row)]
        cost = summary.get("cost")
        started = rows[0].get("created_at", "")
        runs.append({
            "trace_id": trace_id,
            "label": _run_label(trace_id, rows),
            "rows": rows,
            "claims": claims,
            "call_rows": call_rows,
            "state": state,
            "state_word": states_module.status_word(state),
            "verdict": run_verdict,
            "cost": cost,
            "started": _started_word(started),
            "calls": len(call_rows),
        })
    if show == "running":
        runs = [run for run in runs
                if run["state"] in ("FLOWING", "WARNED", "REDIRECTED")]
    elif show == "problems":
        runs = [run for run in runs
                if run["verdict"] in ("Incorrect", "Unverified")]
    return runs


def _top_cost(runs) -> str:
    """The top bar cost: what priced Runs cost, samples left out."""
    total = 0.0
    for run in runs:
        if _is_sample(run["rows"]):
            continue
        if run["cost"] is not None:
            total += run["cost"]
    return f"${total:.2f}"


def _running_sentence(runs, state) -> str:
    """The top bar status sentence, kept current."""
    if state is not None and getattr(state, "_stopped", None):
        return "Emergency stop is on"
    if state is not None and not getattr(state, "watching", True):
        return "Watching is off"
    count = sum(1 for run in runs
                if run["state"] in ("FLOWING", "WARNED", "REDIRECTED"))
    if count == 1:
        return f"Watching · 1 Run running · Cost {_top_cost(runs)}"
    if count > 1:
        return f"Watching · {count} Runs running · Cost {_top_cost(runs)}"
    return f"Watching · No Runs running · Cost {_top_cost(runs)}"


def _alert_strip(runs, state, tier: str, selected: str,
                 dismiss: str = "") -> str:
    """The alert strip: one strip at a time, hidden unless needed."""
    tierq = _tier_query(tier)
    if state is not None and getattr(state, "_stopped", None):
        return (
            '<div class="alert-red"><p>' + _esc(STRIP_ESTOP) + "</p>"
            '<form method="get" action="/controls/emergency-stop">'
            + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
               if tier else "") +
            "<button type=\"submit\">Turn off Emergency stop…</button>"
            "</form></div>")
    waiting = [run for run in runs
               if run["state"] in ("PAUSED", "REDIRECTED")]
    if waiting:
        prefix = waiting[0]["trace_id"][:8]
        return (
            '<div class="alert-amber"><p>'
            + _esc(STRIP_WAITING.format(prefix=prefix)) + "</p>"
            f'<p><a href="/runs/{_esc(waiting[0]["trace_id"])}{tierq}">'
            "Let it go on</a> "
            f'<a href="/{tierq}">Keep it stopped</a></p></div>')
    if state is not None:
        try:
            blocked = state.recently_blocked(limit=20)
        except Exception:
            blocked = []
        blocked = [place for place in blocked if place != dismiss]
        try:
            limited = state.daily_limit is not None
        except Exception:
            limited = False
        if blocked and limited:
            place = blocked[0]
            return (
                '<div class="alert-amber"><p>'
                + _esc(STRIP_BLOCKED.format(place=place)) + "</p>"
                f'<form method="post" action="/settings/allow-place">'
                + _token_field()
                + f'<input type="hidden" name="dest" value="{_esc(place)}">'
                + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                   if tier else "") +
                f"<button type=\"submit\">Allow {_esc(place)}</button>"
                "</form> "
                f'<form method="get" action="/"><input type="hidden" '
                f'name="dismiss" value="{_esc(place)}">'
                + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                   if tier else "") +
                "<button type=\"submit\">Dismiss</button></form></div>")
    return ""


def _frame(title: str, tier: str, tab: str, top_status: str,
           alert: str, body: str, selected: str = "") -> str:
    """The page frame: top bar, tabs, developer line, strip, body."""
    tierq = _tier_query(tier)
    tabs = (
        '<nav class="tabs">'
        f'<a href="/{tierq}">Runs</a> '
        f'<a href="/limits{tierq}">Limits</a> '
        f'<a href="/tools{tierq}">Tools</a></nav>')
    copy_script = (
        "<script>"
        "document.querySelectorAll('button.copy').forEach(function(btn){"
        "btn.addEventListener('click',function(){"
        "var text=btn.getAttribute('data-copy')||'';"
        "if(navigator.clipboard){navigator.clipboard.writeText(text);}"
        "var old=btn.textContent;btn.textContent='Copied';"
        "setTimeout(function(){btn.textContent=old;},2000);});});"
        "</script>")
    if CONSUMER_BUILD:
        copy_script = (
            "<script>"
            "document.querySelectorAll('button.copy').forEach(function(btn){"
            "btn.addEventListener('click',function(){"
            "var text=btn.getAttribute('data-copy')||'';"
            "if(navigator.clipboard){navigator.clipboard.writeText(text);}"
            "var old=btn.textContent;btn.textContent='Copied';"
            "setTimeout(function(){btn.textContent=old;},2000);});});"
            "</script>")
        return ("<html><head><title>Iris</title>" + _STYLE
                + "</head><body>" + body + copy_script + "</body></html>")
    developer = f'<p class="gray">{_esc(DEVELOPER_LINE)}</p>'
    footer = (f'<p class="gray">{_esc(FOOTER_LINE.format(version=_version()))}'
              "</p>")
    paused = selected and _selected_state(selected) == "PAUSED"
    verify_disabled = "" if selected else " disabled"
    verify_tip = (VERIFY_TOOLTIP if selected else PICK_FIRST_TOOLTIP)
    pause_tip = ((RESUME_TOOLTIP if paused else PAUSE_TOOLTIP) if selected
                 else PICK_FIRST_TOOLTIP)
    pause_word = "Resume" if paused else "Pause"
    top = (
        '<div class="topbar"><p><strong>Iris</strong> '
        '<span class="gray">Developer</span></p>'
        f'<p class="status">{_esc(top_status)} <span class="gray" title="'
        + _esc(COST_TOOLTIP) + '">(cost)</span></p>'
        f'<form method="post" action="/runs/{_esc(selected)}/verify{tierq}">'
        + _token_field()
        + f'What was checked: <input name="read_back"> '
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        f'<button type="submit" title="{_esc(verify_tip)}"{verify_disabled}>'
        "Verify</button></form>"
        f'<form method="post" action="/runs/{_esc(selected)}/pause{tierq}">'
        + _token_field()
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        (f'<input type="hidden" name="selected" value="{_esc(selected)}">'
         if selected else "") +
        f'<button type="submit" title="{_esc(pause_tip)}"{verify_disabled}>'
        f"{pause_word}</button></form>"
        '<form method="get" action="/controls/emergency-stop">'
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        f'<button type="submit" class="estop" title="{_esc(EMERGENCY_TOOLTIP)}">'
        "Emergency stop</button></form></div>")
    return (
        "<html><head><title>Iris · Developer</title>"
        + (('<meta http-equiv="refresh" content="1">')
           if tab == "runs" else "")
        + _STYLE + "</head><body>"
        + top + tabs + developer + alert + body + footer + copy_script
        + "</body></html>")


def _selected_state(trace_id: str) -> str:
    """The run state of the selected Run, or empty when none."""
    if not trace_id:
        return ""
    try:
        return states_module.run_state(_rows_for(trace_id))
    except Exception:
        return ""


def _runs_list(runs, tier: str, show: str) -> str:
    """The Run list: one row per Run, newest first."""
    simple = _is_simple(tier)
    advanced = tier == "advanced"
    tierq = _tier_query(tier)
    filter_query = f"?tier={tier}&" if tier else "?"
    if CONSUMER_BUILD:
        head = "<h2>Runs</h2>"
    else:
        head = (
            "<h2>Runs</h2>"
            '<p><a href="/tools/save-journal">Save a copy of the journal'
            "</a></p>"
            '<p class="gray">The file lists what each Run did and what '
            "Iris found. It holds no message text and no keys.</p>")
    head += (
        "<p>Show: "
        f'<a href="{filter_query}show=all">All</a> '
        f'<a href="{filter_query}show=running">Running</a> '
        f'<a href="{filter_query}show=problems" title="Runs where a claim '
        'is Incorrect or Unverified.">Problems</a></p>')
    if not runs:
        empty = ("<p>No Runs yet.</p>")
        if CONSUMER_BUILD:
            empty += (
                "<p>To try Iris now: "
                '<form method="post" action="/tools/run-demo">'
                + _token_field()
                + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                   if tier else "") +
                "<button type=\"submit\">Run the demo</button></form></p>"
                "<p>To watch your own agent, give it this address: "
                f'<code>http://127.0.0.1:{SPOT}</code> '
                '<button type="button" class="copy" '
                f'data-copy="http://127.0.0.1:{SPOT}">Copy</button></p>'
                "<p>Paste it where your agent asks for the address of "
                "its AI service.</p>")
        else:
            empty += (
                "<p>Start the demo agent on the Tools tab, or give your "
                "agent this address: "
                f'<code>http://127.0.0.1:{SPOT}</code> '
                '<button type="button" class="copy" '
                f'data-copy="http://127.0.0.1:{SPOT}">Copy</button></p>'
                "<p>It works with any agent that lets you set the address "
                "of its AI service.</p>")
        return head + empty
    rows = []
    for run in runs:
        tid = run["trace_id"]
        if simple:
            rows.append(
                "<tr>"
                f'<td><a href="/runs/{_esc(tid)}{tierq}" '
                f'title="{_esc(RUN_TOOLTIP)}">{_esc(run["label"])}</a></td>'
                f"<td>{_esc(run['started'])}</td>"
                f'<td><span title="{_esc(_status_tip(run["state"]))}">'
                f"{_esc(run['state_word'])}</span></td>"
                f"<td>{_verdict_cell(run['verdict'])}</td>"
                f"<td>{_money(run['cost'])}</td>"
                "</tr>")
        elif advanced:
            rows.append(
                "<tr>"
                f'<td><a href="/runs/{_esc(tid)}{tierq}" '
                f'title="{_esc(RUN_TOOLTIP)}">'
                f"{_esc(run['label'])}</a></td>"
                f"<td>{_esc(run['started'])}</td>"
                f'<td><span title="{_esc(_status_tip(run["state"]))}">'
                f"{_esc(run['state_word'])}</span></td>"
                f"<td>{_verdict_cell(run['verdict'])}</td>"
                f'<td class="num" title="{_esc(CALLS_TOOLTIP)}">'
                f"{run['calls']}</td>"
                f'<td title="{_esc(WENT_TO_TOOLTIP)}">'
                f"{_went_to_cell(run['call_rows'])}</td>"
                f"<td>{_money(run['cost'])}</td>"
                "</tr>")
        else:
            rows.append(
                "<tr>"
                f'<td><a href="/runs/{_esc(tid)}{tierq}" '
                f'title="{_esc(RUN_TOOLTIP)}">'
                f"{_esc(run['label'])}</a></td>"
                f'<td><span title="{_esc(_status_tip(run["state"]))}">'
                f"{_esc(run['state_word'])}</span></td>"
                f"<td>{_verdict_cell(run['verdict'])}</td>"
                f'<td class="num" title="{_esc(CALLS_TOOLTIP)}">'
                f"{run['calls']}</td>"
                f"<td>{_money(run['cost'])}</td>"
                "</tr>")
    if simple:
        head_row = (
            "<tr>"
            f"<th title=\"{_esc(RUN_TOOLTIP)}\">Run</th>"
            f"<th title=\"{_esc(STARTED_TOOLTIP)}\">Started</th>"
            f"<th title=\"{_esc(STATUS_TOOLTIP)}\">Status</th>"
            f"<th title=\"{_esc(VERDICT_TOOLTIP)}\">Verdict</th>"
            f"<th title=\"{_esc(COST_TOOLTIP)}\">Cost</th>"
            "</tr>")
    elif advanced:
        head_row = (
            "<tr>"
            f"<th title=\"{_esc(RUN_TOOLTIP)}\">Run</th>"
            f"<th title=\"{_esc(STARTED_TOOLTIP)}\">Started</th>"
            f"<th title=\"{_esc(STATUS_TOOLTIP)}\">Status</th>"
            f"<th title=\"{_esc(VERDICT_TOOLTIP)}\">Verdict</th>"
            f"<th title=\"{_esc(CALLS_TOOLTIP)}\">Calls</th>"
            f"<th title=\"{_esc(WENT_TO_TOOLTIP)}\">Went to</th>"
            f"<th title=\"{_esc(COST_TOOLTIP)}\">Cost</th>"
            "</tr>")
    else:
        head_row = (
            "<tr>"
            f"<th title=\"{_esc(RUN_TOOLTIP)}\">Run</th>"
            f"<th title=\"{_esc(STATUS_TOOLTIP)}\">Status</th>"
            f"<th title=\"{_esc(VERDICT_TOOLTIP)}\">Verdict</th>"
            f"<th title=\"{_esc(CALLS_TOOLTIP)}\">Calls</th>"
            f"<th title=\"{_esc(COST_TOOLTIP)}\">Cost</th>"
            "</tr>")
    return head + "<table><thead>" + head_row + "</thead><tbody>" + "".join(rows) + "</tbody></table>"


def _status_tip(state: str) -> str:
    """The Status cell tooltip in plain words."""
    if state == "FLOWING":
        return "The agent is working."
    if state == "WARNED":
        return "The agent is working."
    if state == "REDIRECTED":
        return "The agent is working."
    if state == "PAUSED":
        return "You paused this Run. Its next call is waiting."
    if state == "STOPPED":
        return "This Run can't make more calls."
    if state == "VERIFY_OK":
        return "You paused this Run. Its next call is waiting."
    if state == "BLOCKED":
        return "This Run can't make more calls."
    return "Where the Run is now."


def _claims_table(claims, expanded: bool) -> str:
    """What the agent said, the verdict, what Iris found."""
    head = (
        "<table><thead><tr>"
        "<th>The agent said</th><th>Verdict</th><th>What Iris found</th>"
        "</tr></thead><tbody>")
    if not claims:
        return (head + "</tbody></table>"
                "<p>Iris has not checked any claims yet.</p>")
    rows = []
    for claim in claims:
        cell = (
            "<tr>"
            f"<td>{_esc(claim.get('claim', ''))}</td>"
            f"<td>{_verdict_cell(claim.get('verdict', ''))}</td>"
            f"<td>{_esc(claim.get('found', ''))}</td>"
            "</tr>")
        if expanded:
            took = claim.get("took_ms")
            took_word = f"{took:.0f} ms" if took is not None else "—"
            cell += (
                "<tr><td></td><td colspan=\"2\" class=\"gray\">"
                f"Looked at: {_esc(claim.get('looked_at', ''))}<br>"
                f"Found: {_esc(claim.get('found', ''))}<br>"
                f"Took: {took_word}</td></tr>")
        rows.append(cell)
    return head + "".join(rows) + "</tbody></table>"


def _calls_table(call_rows) -> str:
    """One row per call, oldest first. Metadata only, never bodies."""
    head = (
        "<table><thead><tr>"
        '<th title="The order of the call in this Run.">#</th>'
        '<th title="When the call started.">Time</th>'
        '<th title="What sort of request it was, such as GET or POST. '
        'Encrypted calls say Tunnel.">Kind</th>'
        '<th title="Where the call went.">Place</th>'
        '<th title="Which page or action at that place. Empty for '
        'Tunnel calls.">Path</th>'
        '<th title="How much the agent sent.">Sent</th>'
        '<th title="How much came back.">Received</th>'
        '<th title="How long the call took.">Took</th>'
        '<th title="How the call ended.">Outcome</th>'
        "</tr></thead><tbody>")
    rows = []
    for number, row in enumerate(call_rows, start=1):
        method = row.get("method") or ""
        if method == "CONNECT":
            kind, path = "Tunnel", "—"
        else:
            kind, path = (method or "—"), (row.get("endpoint") or "—")
        rows.append(
            "<tr>"
            f'<td class="num">{number}</td>'
            f"<td>{_esc(_started_word(row.get('created_at', '')))}</td>"
            f"<td>{_esc(kind)}</td>"
            f"<td>{_esc(row.get('dest_host') or '')}</td>"
            f"<td>{_esc(path)}</td>"
            f'<td class="num">{row.get("bytes_in") or 0}</td>'
            f'<td class="num">{row.get("bytes_out") or 0}</td>'
            f"<td>{row.get('latency_seconds') or 0}s</td>"
            f"<td>{_outcome_cell(row)}</td>"
            "</tr>")
    table = head + "".join(rows) + "</tbody></table>"
    table += ("<p class=\"gray\">Tunnel rows are encrypted calls. Iris "
              "passes them through without opening them. It sees where "
              "they go and how big they are, nothing more.</p>")
    return table


def _why_block(claims, verdict: str, stopped: bool) -> str:
    """How Iris decided, one numbered list per claim."""
    if not claims:
        lines = ["<p>Iris has not checked any claims yet.</p>"]
        return "".join(lines)
    parts = []
    for claim in claims:
        steps = [
            "The agent made a claim.",
            "Iris wrote the claim down before doing anything else.",
            f"Iris looked in {claim.get('looked_at') or 'the journal'}.",
            f"{claim.get('found') or 'It could not tell.'}",
            f"Verdict: {claim.get('verdict', 'Unverified')}.",
        ]
        if stopped and claim.get("verdict") == "Incorrect":
            steps.append("Iris stopped the Run.")
        items = "".join(f"<li>{_esc(step)}</li>" for step in steps)
        parts.append(
            f"<p>How Iris decided: {_esc(claim.get('claim', ''))}</p>"
            f"<ol>{items}</ol>")
    parts.append("<p>Why each call is on record: Iris writes each call "
                 "down before it goes out. If Iris stops halfway, the "
                 "journal still shows the call.</p>")
    return "".join(parts)


def _cost_block(call_rows, amount) -> str:
    """The Cost tab: the amount, the meaning, one row per call."""
    head = (f"<p>Cost {_money(amount)}</p>"
            f"<p>{_esc(_cost_meaning(amount))}</p>"
            "<table><thead><tr><th>Call</th><th>Cost</th>"
            "<th>How Iris got it</th></tr></thead><tbody>")
    rows = []
    for number, row in enumerate(call_rows, start=1):
        cost = api_module.attempt_cost(row)
        rows.append(
            "<tr>"
            f'<td class="num">{number}</td>'
            f"<td>{_money(cost)}</td>"
            f"<td>{_esc(_how_got_it(row, cost))}</td>"
            "</tr>")
    return head + "".join(rows) + "</tbody></table>"


def _length_word(rows) -> str:
    """How long the Run lasted: the journal span in plain words."""
    try:
        first = datetime.datetime.fromisoformat(str(rows[0]["created_at"]))
        last = datetime.datetime.fromisoformat(str(rows[-1]["created_at"]))
        seconds = max(0, int((last - first).total_seconds()))
    except (ValueError, TypeError, IndexError):
        return "—"
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _stopped_by_button(rows) -> bool:
    """True when the red button stopped this Run, not a claim."""
    return any(row.get("outcome") == "stopped"
               and "red button" in (row.get("reason") or "")
               for row in rows)


def _run_detail_body(trace_id: str, tier: str, state, request) -> str:
    """The Run detail: header, summary, claim list, and maybe tabs."""
    try:
        rows = _rows_for(trace_id)
    except Exception:
        rows = []
    if not rows:
        tierq = _tier_query(tier)
        return ("<p>No such Run.</p>"
                f'<p><a href="/{tierq}">Back to runs</a></p>')
    run_state = states_module.run_state(rows)
    try:
        claims = _claims_for(trace_id)
    except Exception:
        claims = []
    call_rows = [row for row in rows if _is_call_row(row)]
    verdict = _run_verdict(trace_id, rows)
    state_word = states_module.status_word(run_state)
    label = _run_label(trace_id, rows)
    summary = _summary_for(state_word, verdict, claims,
                           _stopped_by_button(rows))
    tierq = _tier_query(tier)
    try:
        summary_cost = api_module.summarize_costs(rows)["cost"]
    except Exception:
        summary_cost = None
    parts = [f"<h2>{_esc(label)}</h2>",
             f"<p>{_esc(state_word)}</p>"]
    if not CONSUMER_BUILD:
        facts = (f"Started {_esc(_started_word(rows[0].get('created_at', '')))}"
                 f" · Lasted {_length_word(rows)}"
                 f" · {len(call_rows)} calls"
                 f" · Cost {_money(summary_cost)}")
        parts.append(f"<p>{facts}</p>")
    else:
        parts.append(f"<p>Started {_esc(_started_word(rows[0].get('created_at', '')))}</p>")
    parts.append(f"<p>{_esc(summary)}</p>")
    simple = _is_simple(tier)
    if simple:
        parts.append("<h3>What the agent said</h3>")
        parts.append(_claims_table(claims, expanded=False))
    else:
        parts.append("<h3>Claims</h3>")
        parts.append(_claims_table(claims, expanded=not CONSUMER_BUILD))
        parts.append("<h3>Calls</h3>")
        parts.append("<p>Where the calls went. " + _went_to(call_rows) + "</p>")
        parts.append(_calls_table(call_rows))
        parts.append("<h3>Why</h3>")
        parts.append(_why_block(
            claims, verdict, stopped=(state_word == "Stopped")))
        if any(row.get("outcome") == "allowed"
               and row.get("status") == "unknown" for row in call_rows):
            parts.append("<p>This call went out and no answer came "
                         "back. Check the result before trying it "
                         "again.</p>")
        parts.append("<h3>Cost</h3>")
        parts.append(_cost_block(call_rows, summary_cost))
    if state is not None and state.daily_limit is not None:
        unpriced = []
        for row in call_rows:
            if (row.get("outcome") == "blocked"
                    and (row.get("reason") or "").startswith(
                        "unpriceable")):
                dest = row.get("dest_host") or ""
                if dest and dest not in unpriced:
                    unpriced.append(dest)
        for place in unpriced:
            if state.is_allowed_place(place):
                continue
            parts.append(
                "<p>" + _esc(STRIP_BLOCKED.format(place=place)) + "</p>"
                '<form method="post" action="/settings/allow-place">'
                + _token_field()
                + f'<input type="hidden" name="dest" value="{_esc(place)}">'
                + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                   if tier else "") +
                f"<button type=\"submit\">Allow {_esc(place)}</button>"
                "</form>")
    if _is_demo(rows):
        parts.append(
            "<p>That was the demo. It never left this computer. To watch "
            "your own agent, give it this address: "
            f"<code>http://127.0.0.1:{SPOT}</code> "
            '<button type="button" class="copy" '
            f'data-copy="http://127.0.0.1:{SPOT}">Copy</button></p>'
            "<p>Paste it where your agent asks for the address of its "
            "AI service.</p>")
    live, _why = states_module.pause_button_live(run_state)
    if not CONSUMER_BUILD:
        if live:
            parts.append(
                f'<form method="post" action="/runs/{_esc(trace_id)}/pause{tierq}">'
                + _token_field()
                + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                   if tier else "") +
                "<button type=\"submit\">Pause this Run</button></form>"
                "<p>Pressing Pause stops this run unless a human verifies "
                "everything is fine.</p>")
        else:
            parts.append(f"<p>{_esc(_why)}</p>")
        if run_state in ("PAUSED", "REDIRECTED"):
            parts.append(
                "<h2>Verify this Run</h2>"
                "<p>What did you check? Type it in plain words, then press "
                "Verify. Only a fresh Verify after the pause or redirect "
                "lets the Run go again.</p>"
                f'<form method="post" action="/runs/{_esc(trace_id)}/verify{tierq}">'
                + _token_field()
                + 'What was checked: <input name="read_back"> '
                + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                   if tier else "") +
                "<button type=\"submit\">Verify</button></form>")
        if not simple and (live or run_state == "REDIRECTED"):
            parts.append(
                "<h2>Send this Run somewhere else</h2>"
                "<p>Send this Run to a different safe destination.</p>"
                f'<form method="post" action="/runs/{_esc(trace_id)}/redirect{tierq}">'
                + _token_field()
                + 'New destination: <input name="new_dest"> '
                + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                   if tier else "") +
                "<button type=\"submit\">Send them there</button></form>")
    else:
        parts.append("<p>To check or pause this Run, use the buttons "
                     "at the top.</p>")
    parts.append(f'<p><a href="/{tierq}">Back to runs</a></p>')
    return "".join(parts)


app = FastAPI(title="Iris devtool panel (dev only)",
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, run: str = "", dest: str = "",
          verdict: str = "", blocked: str = "", show: str = "all",
          tier: str = "", dismiss: str = ""):
    """The Runs tab. GET only: looking changes nothing."""
    refused = _refused_request(request)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    clean = show if show in ("all", "running", "problems") else "all"
    runs = _collect_runs(show=clean, run=run, dest=dest,
                         verdict=verdict, blocked=blocked)[:100]
    state = _state_of(request)
    top = _running_sentence(runs, state)
    alert = _alert_strip(runs, state, tier, selected="", dismiss=dismiss)
    body = _runs_list(runs, tier, clean)
    return _frame("Runs", tier, "runs", top, alert, body)


def _limits_body(state, tier: str) -> str:
    """The Limits tab: spending, blocked places, send somewhere else."""
    tierq = _tier_query(tier)
    try:
        daily = state.daily_limit if state is not None else None
    except Exception:
        daily = None
    daily_word = f"${daily:.2f}" if daily is not None else "No limit"
    parts = ["<h2>Limits</h2>", "<h3>Spending.</h3>",
             f"<p>{_esc(LIMITS_WARN_LINE)}</p>",
             f"<p>Daily limit: {_esc(daily_word)}</p>"
             '<form method="post" action="/settings/limit">'
             + _token_field()
             + "Daily limit: "
               '<input name="daily" value="'
             + (_esc(f"{daily:.2f}") if daily is not None else "")
             + '"> '
             + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                if tier else "") +
             "<button type=\"submit\">Save limits</button></form>"
             f"<p>{_esc(LIMITS_COUNT_LINE)}</p>"]
    try:
        allowed = state.allowed_places() if state is not None else []
    except Exception:
        allowed = []
    parts.append("<p>Allowed places.</p>")
    if allowed:
        items = "".join(
            f"<li>{_esc(place)} "
            f'<form method="post" action="/settings/unallow-place">'
            + _token_field()
            + f'<input type="hidden" name="dest" value="{_esc(place)}">'
            + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
               if tier else "") +
            "<button type=\"submit\">Remove</button></form></li>"
            for place in allowed)
        parts.append(f"<ul>{items}</ul>")
    else:
        parts.append("<p>No allowed places.</p>")
    parts.append("<p>Recently blocked.</p>")
    try:
        recent = [place for place in state.recently_blocked(limit=20)
                  if not state.is_allowed_place(place)] \
            if state is not None else []
    except Exception:
        recent = []
    if recent:
        items = "".join(
            f"<li>{_esc(place)} "
            f'<form method="post" action="/settings/allow-place">'
            + _token_field()
            + f'<input type="hidden" name="dest" value="{_esc(place)}">'
            + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
               if tier else "") +
            "<button type=\"submit\">Allow</button></form></li>"
            for place in recent)
        parts.append(f"<ul>{items}</ul>")
    else:
        parts.append("<p>Nothing was blocked recently.</p>")
    parts.append("<p>Blocked places.</p>")
    overrides = []
    if state is not None:
        try:
            overrides = sorted(state._overrides)
        except Exception:
            overrides = []
    if overrides:
        items = "".join(
            f"<li>{_esc(place)} "
            f'<form method="post" action="/settings/unblock-network">'
            + _token_field()
            + f'<input type="hidden" name="dest" value="{_esc(place)}">'
            + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
               if tier else "") +
            "<button type=\"submit\">Remove</button></form></li>"
            for place in overrides)
        parts.append(f"<ul>{items}</ul>")
    else:
        parts.append("<p>No blocked places.</p>")
    parts.append(
        '<form method="post" action="/settings/block-network">'
        + _token_field()
        + "Address of the place: "
        '<input name="dest"> '
        "(a destination label, like 127.0.0.1:18080) "
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        "<button type=\"submit\">Block it</button></form>")
    parts.append("<h3>Send a Run somewhere else</h3>")
    parts.append("<p>Send this Run to a different safe destination.</p>"
                 '<form method="post" action="/runs/send">'
                 + _token_field()
                 + "Run: "
                 '<input name="trace_id"> Send them to: '
                 '<input name="new_dest"> '
                 + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
                    if tier else "") +
                 "<button type=\"submit\">Next</button></form>")
    return "".join(parts)


def _tools_body(state, tier: str) -> str:
    """The Tools tab: developer-only controls, each with its purpose."""
    parts = ["<h2>Tools</h2>"]
    try:
        watching = state.watching if state is not None else True
    except Exception:
        watching = True
    parts.append(
        "<h3>Demo agent</h3>"
        "<p>Starts the demo agent. It runs on this computer. "
        "It costs $0.00.</p>"
        '<form method="post" action="/tools/run-demo">'
        + _token_field()
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        "<button type=\"submit\">Run the demo</button></form>")
    parts.append(
        "<h3>Test places</h3>"
        "<p>Pretend places your agent can call. They run on this "
        "computer. Nothing leaves it.</p>"
        + _test_places_table(tier))
    parts.append(
        "<h3>Sample Runs</h3>"
        "<p>Add made-up Runs so you can see how the screens look. They "
        "are marked Sample. They are never real.</p>"
        '<form method="post" action="/tools/samples">'
        + _token_field()
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        '<button type="submit" name="action" value="add">Add sample Runs'
        "</button> "
        '<button type="submit" name="action" value="remove">Remove sample '
        "Runs</button></form>")
    logdetail = "warnings"
    if state is not None:
        try:
            saved = db_module.get_setting(state.conn, "log_detail")
            if saved in ("warnings", "normal", "everything"):
                logdetail = saved
        except Exception:
            pass
    parts.append(
        "<h3>Log detail</h3><p>More detail never adds message text "
        "or keys.</p>"
        '<form method="post" action="/tools/log-detail">'
        + _token_field() + "<p>"
        + "".join(
            f'<label><input type="radio" name="level" value="{level}"'
            f"{' checked' if level == logdetail else ''}> "
            f"{label}</label><br>"
            for level, label in (
                ("warnings", "Warnings and errors only"),
                ("normal", "Normal"),
                ("everything", "Everything")))
        + "</p>"
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        "<button type=\"submit\">Save</button></form>"
        "<p class=\"gray\">The log is a file in the journal folder.</p>")
    parts.append("<h3>Settings in use</h3>"
                 "<p>Where each setting comes from. This table is "
                 "read-only.</p>" + _settings_table(state))
    parts.append(
        "<h3>Watching</h3>"
        "<p>Off means agents that use Iris can't reach anything. An "
        "agent that doesn't use Iris is not affected, and Iris can't "
        "see it.</p>"
        '<form method="post" action="/tools/watching">'
        + _token_field()
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        (f'<input type="hidden" name="on" value="off">'
         f"<button type=\"submit\">Watching: "
         f"{'On' if watching else 'Off'}</button>"
         if watching else
         '<input type="hidden" name="on" value="on">'
         "<button type=\"submit\">Watching: Off</button>")
        + "</form>")
    return "".join(parts)


def _settings_file_path() -> str:
    """The settings file beside the journal, when there is one."""
    try:
        folder = os.path.dirname(os.path.abspath(api_module.DB_PATH))
    except Exception:
        folder = "."
    return os.path.join(folder, "iris-settings.json")


def _settings_file_values() -> dict:
    """Settings read from the settings file, or empty when none."""
    import json as _json
    try:
        with open(_settings_file_path(), encoding="utf-8") as handle:
            data = _json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _settings_in_use(state) -> list:
    """(setting, value, source) rows for the read-only table."""
    rows = []
    rows.append(("The spot Iris listens on", str(SPOT), "Default"))
    try:
        folder = os.path.dirname(os.path.abspath(api_module.DB_PATH))
    except Exception:
        folder = ""
    if os.environ.get("PROXY_DB"):
        rows.append(("Where Iris keeps its journal", folder,
                     "Environment"))
    else:
        rows.append(("Where Iris keeps its journal", folder, "Default"))
    try:
        daily = state.daily_limit if state is not None else None
    except Exception:
        daily = None
    filed = _settings_file_values()
    if daily is not None:
        rows.append(("Daily limit", f"${daily:.2f}", "Settings file"
                     if "daily_limit" in filed else "Default"))
    elif "daily_limit" in filed:
        rows.append(("Daily limit", str(filed["daily_limit"]),
                     "Settings file"))
    else:
        rows.append(("Daily limit", "No limit", "Default"))
    level = "Warnings and errors only"
    source = "Default"
    if state is not None:
        try:
            saved = db_module.get_setting(state.conn, "log_detail")
        except Exception:
            saved = None
        if saved in ("warnings", "normal", "everything"):
            level = {"warnings": "Warnings and errors only",
                     "normal": "Normal",
                     "everything": "Everything"}[saved]
            source = ("Settings file" if "log_detail" in filed
                      else "Default")
    rows.append(("Log detail", level, source))
    keep = "Until I delete them"
    keep_source = "Default"
    if state is not None:
        try:
            saved_keep = db_module.get_setting(state.conn, "journal_keep")
        except Exception:
            saved_keep = None
        if saved_keep in ("30 days", "1 year", "Until I delete them"):
            keep = saved_keep
            keep_source = ("Settings file" if "journal_keep" in filed
                           else "Default")
    rows.append(("How long to keep the journal", keep, keep_source))
    return rows


def _settings_table(state) -> str:
    """Where each setting comes from. This table is read-only."""
    head = ("<table><thead><tr><th>Setting</th><th>Value</th>"
            "<th>Where it came from</th></tr></thead><tbody>")
    rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{_esc(value)}</td>"
        f"<td>{_esc(source)}</td></tr>"
        for name, value, source in _settings_in_use(state))
    table = head + rows + "</tbody></table>"
    table += ("<p>To change a setting, edit the settings file. The path "
              "is below.</p>"
              f"<p><code>{_esc(_settings_file_path())}</code> "
              "<button type=\"button\" class=\"copy\" "
              f"data-copy=\"{_esc(_settings_file_path())}\">Copy</button></p>"
              '<form method="post" action="/tools/reload-settings">'
              + _token_field() +
              "<button type=\"submit\">Reload settings file</button>"
              "</form>")
    return table


_TEST_PLACES = [
    ("always", "Answers every call right away."),
    ("slow", "Waits 5 seconds, then answers."),
    ("fails", "Refuses every call."),
    ("quiet", "Takes the call, then never answers. Use it to see "
              "Unverified."),
]
_TEST_PLACE_SERVERS: dict = {}


def _test_place_port(name: str) -> int:
    """Each test place has its own spot past the panel's."""
    index = [place for place, _words in _TEST_PLACES].index(name)
    return SPOT + 1 + index


def _test_places_table(tier: str) -> str:
    """The test places table: name, what it does, address, switch."""
    rows = []
    for name, words in _TEST_PLACES:
        address = f"http://127.0.0.1:{_test_place_port(name)}/{name}"
        live = name in _TEST_PLACE_SERVERS
        if live:
            address_cell = (
                f"<code>{_esc(address)}</code> "
                "<button type=\"button\" class=\"copy\" "
                f"data-copy=\"{_esc(address)}\">Copy</button>")
        else:
            address_cell = "—"
        rows.append(
            "<tr>"
            f"<td><code>{_esc(name)}</code></td>"
            f"<td>{_esc(words)}</td>"
            f"<td>{address_cell}</td>"
            "<td>"
            f'<form method="post" action="/tools/test-place">'
            + _token_field()
            + f'<input type="hidden" name="name" value="{_esc(name)}">'
            + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
               if tier else "")
            + f'<input type="hidden" name="on" value="'
            + ("off" if live else "on") + '">'
            + ("<button type=\"submit\">On</button>"
               if live else "<button type=\"submit\">Off</button>")
            + "</form></td></tr>")
    return ("<table><thead><tr><th>Name</th><th>What it does</th>"
            "<th>Address</th><th></th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


@app.get("/runs/{trace_id}", response_class=HTMLResponse)
def run_detail(trace_id: str, tier: str = "", request=None):
    """One Run: the claim list, the calls, why, and cost.

    Simple shows the claim list only. Anything else shows all four
    sections. No tier means show everything.
    """
    if request is not None:
        refused = _refused_request(request)
        if refused is not None:
            return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request) if request is not None else None
    body = _run_detail_body(trace_id, tier, state, request)
    if body.startswith("<p>No such Run.</p>"):
        return HTMLResponse(
            "<html><body>" + body + "</body></html>", status_code=404)
    if CONSUMER_BUILD:
        return _frame("Run", tier, "runs", "", "", body,
                      selected=trace_id)
    runs = _collect_runs()
    top = _running_sentence(runs, state)
    alert = _alert_strip(runs, state, tier, selected=trace_id, dismiss="")
    return _frame("Run", tier, "runs", top, alert, body,
                  selected=trace_id)


@app.get("/limits", response_class=HTMLResponse)
def limits_page(request: Request, tier: str = ""):
    """The Limits tab. GET only: looking changes nothing."""
    refused = _refused_request(request)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    runs = _collect_runs()
    top = _running_sentence(runs, state)
    alert = _alert_strip(runs, state, tier, selected="", dismiss="")
    return _frame("Limits", tier, "limits", top, alert,
                  _limits_body(state, tier))


@app.get("/tools", response_class=HTMLResponse)
def tools_page(request: Request, tier: str = ""):
    """The Tools tab. GET only: looking changes nothing."""
    refused = _refused_request(request)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    runs = _collect_runs()
    top = _running_sentence(runs, state)
    alert = _alert_strip(runs, state, tier, selected="", dismiss="")
    return _frame("Tools", tier, "tools", top, alert,
                  _tools_body(state, tier))


def _watch_only(request, tier: str = ""):
    """503 when no live proxy sits beside the panel."""
    tierq = _tier_query(tier)
    return HTMLResponse(
        "<html><body><p>Panel is watching only: start it beside "
        "a live proxy to change it.</p>"
        f'<p><a href="/{tierq}">Back to runs</a></p></body></html>',
        status_code=503)


@app.post("/runs/{trace_id}/pause")
def pause_button(trace_id: str, request: Request, tier: str = "",
                 token: str = Form("")):
    """The Pause button: calls the plain human-only function, nothing else.

    Dev only, loopback only. This is one of the control routes in the
    panel app, and the main API stays GET-only: no agent can reach it
    through the proxy, and nothing here is reachable off this machine.
    The tier rides the redirect back to the same tier.
    """
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    tierq = _tier_query(tier)
    state = _state_of(request)
    if state is None:
        return HTMLResponse(
            "<html><body><p>Panel is watching only: start it beside "
            "a live proxy to pause.</p>"
            f'<p><a href="/{tierq}">Back to runs</a></p></body></html>',
            status_code=503)
    seam_pause_run(state, trace_id, reason="human pause")
    return RedirectResponse(f"/runs/{trace_id}{tierq}", status_code=303)


@app.post("/runs/{trace_id}/verify")
def verify_button(trace_id: str, request: Request,
                  read_back: str = Form(""), tier: str = "",
                  token: str = Form("")):
    """The Verify button: stamps the gate, nothing else.

    Dev only, loopback only. Same shape as the Pause button: the panel
    app holds the live proxy state, and the route calls the seam on
    it. A refused Verify changes nothing and says so on its own page.
    The tier rides the redirect back to the same tier.
    """
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    tierq = _tier_query(tier)
    state = _state_of(request)
    if state is None:
        return HTMLResponse(
            "<html><body><p>Panel is watching only: start it beside "
            "a live proxy to verify.</p>"
            f'<p><a href="/{tierq}">Back to runs</a></p></body></html>',
            status_code=503)
    if devpanel_verify(state, trace_id, read_back=read_back):
        return RedirectResponse(f"/runs/{trace_id}{tierq}",
                                status_code=303)
    return HTMLResponse(
        "<html><body><p>That Verify was refused. The Run must be "
        "paused or redirected, and the read-back must say what was "
        "checked.</p>"
        f'<p><a href="/runs/{trace_id}{tierq}">Back to run</a></p>'
        "</body></html>",
        status_code=400)


@app.post("/runs/{trace_id}/redirect")
def redirect_button(trace_id: str, request: Request,
                    new_dest: str = Form(""), tier: str = "",
                    token: str = Form("")):
    """Send this Run somewhere else: the plain human-only setter.

    Dev only, loopback only. Same shape as the Pause button: the panel
    app holds the live proxy state, and the route calls the setter on
    it. A refused destination changes nothing and says so on its own
    page. The tier rides the redirect back to the same tier.
    """
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    tierq = _tier_query(tier)
    state = _state_of(request)
    if state is None:
        return HTMLResponse(
            "<html><body><p>Panel is watching only: start it beside "
            "a live proxy to send it somewhere else.</p>"
            f'<p><a href="/{tierq}">Back to runs</a></p></body></html>',
            status_code=503)
    if state.redirect_run(trace_id, new_dest):
        return RedirectResponse(f"/runs/{trace_id}{tierq}",
                                status_code=303)
    return HTMLResponse(
        "<html><body><p>That destination was refused. Use a destination "
        "Iris already allows.</p>"
        f'<p><a href="/runs/{trace_id}{tierq}">Back to run</a></p>'
        "</body></html>",
        status_code=400)


@app.post("/settings/spend-cap")
def cap_button(request: Request, dest: str = Form(""),
               dollars: str = Form(""), token: str = Form("")):
    """The cap form: calls the plain human-only setter, nothing else.

    Dev only, loopback only. Same shape as the Pause button. A refused
    value changes nothing and says so on its own page.
    """
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return HTMLResponse(
            "<html><body><p>Panel is watching only: start it beside "
            "a live proxy to set a cap.</p>"
            '<p><a href="/">Back to runs</a></p></body></html>',
            status_code=503)
    if state.set_cap(dest, dollars):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        "<html><body><p>That cap was refused. Use a destination label "
        "Iris already allows, like 127.0.0.1:18080, not a web address, "
        "and a plain dollar number above zero.</p>"
        '<p><a href="/">Back to runs</a></p></body></html>',
        status_code=400)


@app.post("/settings/block-network")
def block_network_button(request: Request, dest: str = Form(""),
                         token: str = Form("")):
    """The block form: calls the plain human-only setter, nothing else.

    Dev only, loopback only. Same shape as the Pause button. A refused
    destination changes nothing and says so on its own page.
    """
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return HTMLResponse(
            "<html><body><p>Panel is watching only: start it beside "
            "a live proxy to block a network.</p>"
            '<p><a href="/">Back to runs</a></p></body></html>',
            status_code=503)
    if state.set_override(dest):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        "<html><body><p>That block was refused. Use a destination "
        "label Iris already allows, like 127.0.0.1:18080, not a web "
        "address.</p>"
        '<p><a href="/">Back to runs</a></p></body></html>',
        status_code=400)


@app.post("/settings/relay-timeout")
def relay_timeout_button(request: Request, seconds: str = Form(""),
                         token: str = Form("")):
    """The cutoff form: calls the plain human-only setter, nothing else.

    The page no longer links it, but the setter stays for the API and
    the tests. Dev only, loopback only. A refused value changes
    nothing and says so on its own page.
    """
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request)
    if state.set_relay_timeout(seconds):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        "<html><body><p>That cutoff was refused. Use 5 to 3600 "
        "seconds, a plain number.</p>"
        '<p><a href="/">Back to runs</a></p></body></html>',
        status_code=400)


@app.get("/controls/emergency-stop", response_class=HTMLResponse)
def emergency_stop_confirm(request: Request, tier: str = ""):
    """The turn-off page: looking changes nothing.

    Dev only, loopback only. GET only shows the page. The stop itself
    happens on POST, so no link or refresh can stop everything by
    accident.
    """
    refused = _refused_request(request)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    tierq = _tier_query(tier)
    if _state_of(request) is None:
        return HTMLResponse(
            "<html><body><p>Panel is watching only: start it beside "
            "a live proxy to stop everything.</p>"
            f'<p><a href="/{tierq}">Back to runs</a></p></body></html>',
            status_code=503)
    return HTMLResponse(
        "<html><head><title>Let agents work again?</title>"
        + _STYLE + "</head><body>"
        "<h1>Let agents work again?</h1>"
        "<p>Agents that use Iris will be able to make calls again. Runs "
        "that were stopped stay stopped.</p>"
        '<form method="post" action="/controls/emergency-stop">'
        + _token_field()
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        "<button type=\"submit\" name=\"keep\" value=\"1\">Keep it on"
        "</button> "
        "<button type=\"submit\" name=\"off\" value=\"1\">"
        "Let agents work again</button></form>"
        f'<p><a href="/{tierq}">Back to runs</a></p>'
        "</body></html>")


@app.post("/controls/emergency-stop")
def emergency_stop_execute(request: Request, tier: str = "",
                           token: str = Form(""),
                           keep: str = Form("")):
    """The stop button: calls the plain human-only setter, nothing else.

    Dev only, loopback only. Same shape as the Pause button, with one
    difference: the human already confirmed on the GET page. Keep it
    on changes nothing and leaves the stop in place.
    """
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    tierq = _tier_query(tier)
    if keep:
        return RedirectResponse(f"/{tierq}", status_code=303)
    state = _state_of(request)
    if state is None:
        return HTMLResponse(
            "<html><body><p>Panel is watching only: start it beside "
            "a live proxy to stop everything.</p>"
            f'<p><a href="/{tierq}">Back to runs</a></p></body></html>',
            status_code=503)
    state.red_button()
    return RedirectResponse(f"/{tierq}", status_code=303)


@app.get("/map", response_class=HTMLResponse)
def swarm_map(request: Request, tier: str = ""):
    """The swarm map: one inline SVG, zero JavaScript.

    Dev only, loopback only. A read of the journal, nothing more:
    the latest 20 Runs, one node per Run, one node per destination.
    The tier only rides the back link home.
    """
    refused = _refused_request(request)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    summaries = api_module.list_traces(limit=20)["traces"]
    traces = [{"trace_id": s["trace_id"], "rows": _rows_for(s["trace_id"])}
              for s in summaries]
    built = map_module.build_map(traces)
    return HTMLResponse(
        "<html><head><title>Swarm map, dev only</title>"
        + _STYLE + "</head><body>"
        "<h1>Swarm map</h1>"
        "<p>The map below is showing the latest 20 Runs. Click a Run "
        "to open it. Click a destination to filter the run list to "
        "it.</p>"
        + map_module.to_svg(built) +
        f'<p><a href="/{_tier_query(tier)}">Back to runs</a></p>'
        "</body></html>")


@app.post("/settings/limit")
def limit_button(request: Request, daily: str = Form(""),
                 tier: str = Form(""), token: str = Form("")):
    """Save the daily limit. One page, no confirmation beyond saving."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    text = (daily or "").strip()
    if not text or text.lower() in ("no limit", "none", "0", "$0",
                                    "$0.00"):
        state.set_daily_limit(None)
    else:
        cleaned = text.lstrip("$").strip()
        try:
            state.set_daily_limit(float(cleaned))
        except (TypeError, ValueError):
            return HTMLResponse(
                "<html><body><p>That limit was refused. Use a plain "
                "dollar number above zero, or clear it for no limit."
                "</p>"
                f'<p><a href="/limits{_tier_query(tier)}">Back to limits'
                "</a></p></body></html>",
                status_code=400)
    return RedirectResponse(f"/limits{_tier_query(tier)}",
                            status_code=303)


@app.post("/settings/allow-place")
def allow_place_button(request: Request, dest: str = Form(""),
                       tier: str = Form(""), token: str = Form("")):
    """Allow one place in one click. No typing, no confirmation."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    state.allow_place(dest or "")
    return RedirectResponse(f"/limits{_tier_query(tier)}",
                            status_code=303)


@app.post("/settings/unallow-place")
def unallow_place_button(request: Request, dest: str = Form(""),
                         tier: str = Form(""), token: str = Form("")):
    """Stop allowing one place. The Undo behind the strip button."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    state.unallow_place(dest or "")
    return RedirectResponse(f"/limits{_tier_query(tier)}",
                            status_code=303)


@app.post("/settings/unblock-network")
def unblock_network_button(request: Request, dest: str = Form(""),
                           tier: str = Form(""), token: str = Form("")):
    """Let agents reach one blocked place again. Human only."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    state.clear_override(dest or "")
    return RedirectResponse(f"/limits{_tier_query(tier)}",
                            status_code=303)


@app.post("/runs/send")
def send_run_button(request: Request, trace_id: str = Form(""),
                    new_dest: str = Form(""), tier: str = Form(""),
                    token: str = Form("")):
    """Send a Run somewhere else: pick, then confirm, then send."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    tierq = _tier_query(tier)
    prefix = (trace_id or "")[:8]
    return HTMLResponse(
        "<html><body>"
        f"<p>From now on, Run {_esc(prefix)}'s calls go to "
        f"{_esc(new_dest)} instead. Do this?</p>"
        f'<form method="post" action="/runs/{_esc(trace_id)}/redirect{tierq}">'
        + _token_field()
        + f'<input type="hidden" name="new_dest" value="{_esc(new_dest)}">'
        + (f'<input type="hidden" name="tier" value="{_esc(tier)}">'
           if tier else "") +
        "<button type=\"submit\">Send them there</button></form>"
        f'<p><a href="/limits{tierq}">Cancel</a></p>'
        "</body></html>")


def _start_test_place(name: str) -> bool:
    """Start one pretend place on this computer. Nothing leaves it."""
    import http.server
    import time as _time

    if name not in [place for place, _words in _TEST_PLACES]:
        return False
    if name in _TEST_PLACE_SERVERS:
        return True

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            if name == "slow":
                _time.sleep(5)
            if name == "fails":
                body = b"refused by the test place\n"
                self.send_response(500)
            elif name == "quiet":
                _time.sleep(3600)
                return
            else:
                body = b"answered by the test place\n"
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self._answer()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                self.rfile.read(length)
            except Exception:
                pass
            self._answer()

        def log_message(self, *args):
            pass

    try:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", _test_place_port(name)), _Handler)
    except OSError:
        return False
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name=f"iris-test-place-{name}")
    thread.start()
    _TEST_PLACE_SERVERS[name] = (server, thread)
    return True


def _stop_test_place(name: str) -> bool:
    """Stop one pretend place. Calls to it fail after this."""
    entry = _TEST_PLACE_SERVERS.pop(name, None)
    if entry is None:
        return True
    server, thread = entry
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass
    thread.join(timeout=10.0)
    return True


def _add_sample_runs(state) -> int:
    """Add three made-up Runs: Correct, Incorrect, Unverified.

    Never real: every row carries the sample marker, the Runs cost
    $0.00 on loopback, and the journal save leaves them out unless
    the human ticks the box.
    """
    import uuid
    verdicts = [("Correct", "success"), ("Incorrect", "failure"),
                ("Unverified", "unknown")]
    made = 0
    for number, (verdict, status) in enumerate(verdicts, start=1):
        trace_id = "sample-" + uuid.uuid4().hex[:12]
        db_module.log_attempt(
            state.conn, trace_id=trace_id, task_number=1, attempt=1,
            status="success", outcome="allowed",
            dest_host="127.0.0.1", endpoint="/", method="GET",
            bytes_in=9, bytes_out=20, latency_seconds=0.001)
        key = f"verify call sample:{number}"
        state._control_row(trace_id, "VERIFY", "", "", "unknown",
                           "verified", key, "verified")
        state._control_row(trace_id, "VERIFY", "", "", status,
                           "verified", f"verify {verdict}", "verified")
        import datetime
        now = datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds")
        db_module.save_claim(
            state.conn, trace_id, f"Sample claim {number} (made up)",
            verdict, "sample data", "Made up for the demo screens.",
            0.0, now)
        made += 1
    return made


def _remove_sample_runs(state) -> int:
    """Remove all sample Runs. Real Runs are never touched."""
    traces = [row[0] for row in state.conn.execute(
        "SELECT DISTINCT trace_id FROM attempts WHERE reason LIKE "
        "'verify call sample%'").fetchall()]
    for trace_id in traces:
        state.conn.execute("DELETE FROM attempts WHERE trace_id = ?",
                           (trace_id,))
        state.conn.execute("DELETE FROM claims WHERE trace_id = ?",
                           (trace_id,))
    state.conn.commit()
    return len(traces)


@app.post("/tools/run-demo")
def run_demo_button(request: Request, tier: str = Form(""),
                    token: str = Form("")):
    """Start the demo agent. It runs on this computer. Costs $0.00."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    from appmode import demo as demo_module
    folder = os.path.dirname(os.path.abspath(api_module.DB_PATH))
    notebook = os.path.join(folder or ".", "demo-notebook.json")
    thread = threading.Thread(
        target=demo_module.run_demo, args=(state, notebook),
        kwargs={"interval_seconds": 2.0}, daemon=True,
        name="iris-demo")
    thread.start()
    return RedirectResponse(f"/{_tier_query(tier)}", status_code=303)


@app.post("/tools/test-place")
def test_place_button(request: Request, name: str = Form(""),
                      on: str = Form(""), tier: str = Form(""),
                      token: str = Form("")):
    """Flip one pretend place on or off. Loopback only."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    if on == "on":
        _start_test_place(name)
    else:
        _stop_test_place(name)
    return RedirectResponse(f"/tools{_tier_query(tier)}",
                            status_code=303)


@app.post("/tools/samples")
def samples_button(request: Request, action: str = Form(""),
                   tier: str = Form(""), token: str = Form("")):
    """Add or remove the made-up sample Runs."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    if action == "add":
        _add_sample_runs(state)
    elif action == "remove":
        _remove_sample_runs(state)
    return RedirectResponse(f"/tools{_tier_query(tier)}",
                            status_code=303)


@app.post("/tools/log-detail")
def log_detail_button(request: Request, level: str = Form(""),
                      tier: str = Form(""), token: str = Form("")):
    """Set how much the log says. Never message text or keys."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    if level in ("warnings", "normal", "everything"):
        db_module.set_setting(state.conn, "log_detail", level)
    return RedirectResponse(f"/tools{_tier_query(tier)}",
                            status_code=303)


@app.post("/tools/watching")
def watching_button(request: Request, on: str = Form(""),
                    tier: str = Form(""), token: str = Form("")):
    """Turn watching off or on. One click, no confirmation."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    state.set_watching(on == "on")
    if CONSUMER_BUILD:
        return RedirectResponse(f"/{_tier_query(tier)}",
                                status_code=303)
    return RedirectResponse(f"/tools{_tier_query(tier)}",
                            status_code=303)


@app.post("/tools/reload-settings")
def reload_settings_button(request: Request, tier: str = Form(""),
                           token: str = Form("")):
    """Reload the settings file. A new spot needs a restart."""
    refused = _refused_request(request, token)
    if refused is not None:
        return refused
    gone = _dev_only(request, tier) or _tier_gone(tier)
    if gone is not None:
        return gone
    state = _state_of(request)
    if state is None:
        return _watch_only(request, tier)
    filed = _settings_file_values()
    if "daily_limit" in filed:
        try:
            state.set_daily_limit(float(filed["daily_limit"]))
        except (TypeError, ValueError):
            pass
    if filed.get("log_detail") in ("warnings", "normal", "everything"):
        db_module.set_setting(state.conn, "log_detail",
                              filed["log_detail"])
    if filed.get("journal_keep") in ("30 days", "1 year",
                                     "Until I delete them"):
        db_module.set_setting(state.conn, "journal_keep",
                              filed["journal_keep"])
    return HTMLResponse(
        "<html><body><p>Reloaded. A new spot for Iris needs a restart."
        "</p>"
        f'<p><a href="/tools{_tier_query(tier)}">Back to tools</a></p>'
        "</body></html>")


@app.get("/tools/save-journal")
def save_journal(request: Request, include: str = ""):
    """Download the journal. No message text, no keys, ever."""
    refused = _refused_request(request)
    if refused is not None:
        return refused
    gone = _dev_only(request)
    if gone is not None:
        return gone
    import datetime
    import json as _json
    day = datetime.datetime.now(
        datetime.timezone.utc).date().isoformat()
    runs = []
    for run in _collect_runs():
        if _is_sample(run["rows"]) and include != "1":
            continue
        runs.append({
            "run": run["label"],
            "trace_id": run["trace_id"],
            "started": run["started"],
            "status": run["state_word"],
            "verdict": run["verdict"],
            "cost": run["cost"],
            "claims": [
                {"claim": claim.get("claim", ""),
                 "verdict": claim.get("verdict", ""),
                 "looked_at": claim.get("looked_at", ""),
                 "found": claim.get("found", "")}
                for claim in run["claims"]],
            "calls": [
                {"time": row.get("created_at", ""),
                 "kind": ("Tunnel" if row.get("method") == "CONNECT"
                          else (row.get("method") or "")),
                 "place": row.get("dest_host") or "",
                 "path": ("" if row.get("method") == "CONNECT"
                          else (row.get("endpoint") or "")),
                 "sent": row.get("bytes_in") or 0,
                 "received": row.get("bytes_out") or 0,
                 "took": row.get("latency_seconds") or 0,
                 "outcome": _outcome_cell(row)}
                for row in run["call_rows"]],
        })
    body = _json.dumps({"journal": runs, "as_of": day}, indent=2)
    return HTMLResponse(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="iris-journal-{day}"'})


def serve_dev(host: str = "127.0.0.1", panel_port: int = 8000,
              proxy_port: int = 8888, db_path=None):
    """Start the watched proxy and the dev panel in one local process.

    Both listen on loopback only. The panel reads the same journal file
    the proxy writes, and the buttons call the seams with the same
    live proxy state, so what the panel shows is what Iris enforces.
    The dev build keeps every route: nothing is hidden here.
    """
    import uvicorn

    db_path = db_path or api_module.DB_PATH
    api_module.DB_PATH = db_path
    proxy = proxy_module.serve(host, proxy_port, db_path)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    app.state.proxy_state = proxy.state
    install_identity(panel_port)
    set_consumer_build(False)
    print(f"iris proxy listening on {host}:{proxy_port} db={db_path}")
    print(f"dev panel at http://{host}:{panel_port}/ "
          f"(dev panel, this computer only)")
    uvicorn.run(app, host=host, port=panel_port)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--proxy-port", type=int, default=8888)
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    serve_dev(args.host, args.port, args.proxy_port, args.db)


if __name__ == "__main__":
    main()
