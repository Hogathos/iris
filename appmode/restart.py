"""Restart an agent after connect, or say how in plain words.

v1: Cursor on Windows gets a real restart. Iris closes Cursor
and reopens it. Claude Code and Muse Code run in a terminal,
Continue runs inside VS Code: Iris cannot restart those from
outside, so Iris says how in plain words instead. Cursor off
Windows gets the same manual instruction. Unknown agents refuse
in plain words.

The restart decision is injectable: find_exe locates the agent
binary, restart_process kills and relaunches it. Tests stub both,
so no test ever touches a live process. The property test over
restart_agent is the oracle for this decision: a real restart
happens only for Cursor on Windows with an exe found.
"""

from __future__ import annotations

import os
import sys

from appmode.connect import Refused

# Process name Iris kills before relaunching Cursor on Windows.
CURSOR_PROCESS_NAME = "Cursor.exe"


def _default_find_exe(agent_id: str) -> str | None:
    """The agent binary path, or None when Iris cannot restart it.

    Only Cursor on Windows has a path: the exe under the user's
    home, LOCALAPPDATA, or Program Files. Every other agent
    returns None, so the caller falls back to a manual
    instruction.
    """
    if agent_id != "cursor" or sys.platform != "win32":
        return None
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "AppData", "Local", "Programs", "cursor", "Cursor.exe"),
        os.path.join(home, "AppData", "Local", "Cursor", "Cursor.exe"),
    ]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(os.path.join(local, "Programs", "cursor", "Cursor.exe"))
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(var, "")
        if root:
            candidates.append(os.path.join(root, "Cursor", "Cursor.exe"))
            candidates.append(os.path.join(root, "cursor", "Cursor.exe"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def _default_restart_process(exe_path: str, process_name: str) -> bool:
    """Kill the named process, then relaunch the exe. True when run.

    Windows only: taskkill stops the running agent, Popen starts
    it again. A missing process is fine: taskkill may report
    nothing to kill, and the relaunch still runs.
    """
    import subprocess

    subprocess.run(["taskkill", "/F", "/IM", process_name], capture_output=True, check=False)
    subprocess.Popen([exe_path])
    return True


def _manual_instruction(agent_id: str) -> str:
    """Plain-words restart steps for an agent Iris cannot restart."""
    if agent_id == "claude-code":
        return "Claude Code runs in your terminal. Quit this session and start a new one to pick up the proxy change."
    if agent_id == "muse-code":
        return "Muse Code runs in your terminal. Quit this session and start a new one to pick up the proxy change."
    if agent_id == "continue":
        return (
            "Continue runs inside VS Code. Reload the VS Code window (Developer: Reload Window) to pick up the proxy change."
        )
    return "Quit Cursor fully and reopen it to pick up the proxy change."


def restart_agent(agent_id: str, platform: str | None = None, find_exe=None, restart_process=None) -> str:
    """Restart the agent, or return a manual instruction.

    A real restart happens only for Cursor on Windows with an exe
    found: Iris closes Cursor and reopens it. Every other case
    returns plain words and calls no process function. Unknown
    agents refuse in plain words.
    """
    from appmode.connect import AGENT_NAMES

    if agent_id not in AGENT_NAMES:
        raise Refused(f"Unknown agent: {agent_id}. Iris knows {', '.join(sorted(AGENT_NAMES))}.")
    if platform is None:
        platform = sys.platform
    if agent_id != "cursor" or platform != "win32":
        return _manual_instruction(agent_id)
    finder = find_exe or _default_find_exe
    exe_path = finder(agent_id)
    if not exe_path:
        return _manual_instruction(agent_id)
    runner = restart_process or _default_restart_process
    runner(exe_path, CURSOR_PROCESS_NAME)
    return "Cursor is restarting."
