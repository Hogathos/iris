"""One-click connect: write an agent's proxy setting to Iris.

Iris connects a known agent to the Iris proxy with one click. Iris
backs up the agent's config before it writes, shows the user what
changed in plain words, and restores the backup on disconnect.
If the agent already has a proxy setting, Iris refuses in plain
words and changes nothing. If the agent is not known, Iris says so
in plain words.

Agents in v1: Claude Code, Cursor, Continue. Each stores its proxy
in a plain file, so Iris edits the file. Config paths take a home
dir, so tests run under a temp home and never touch a live config.

Loopback only. Metadata only. No bodies, no keys. Plain words only.
"""
from __future__ import annotations

import json
import os
import sys

import yaml

# The backup suffix: the original bytes, kept beside the config.
BACKUP_SUFFIX = ".iris-backup"

# Loopback bypass for a fresh Claude Code proxy block. The agent
# must not send Iris's own traffic back through the proxy.
LOOPBACK_BYPASS = "localhost,127.0.0.1"

# Known agents: id to plain display name. The id is the file key.
# The name is what the human reads.
AGENT_NAMES = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "continue": "Continue",
}


class Refused(Exception):
    """Iris refused a connect or disconnect, in plain words.

    Raised, never returned: the message says what Iris would not
    do and why, and the config is unchanged when this leaves.
    """


def _agent_name(agent_id: str) -> str:
    """The plain display name, or refusal for an unknown agent."""
    try:
        return AGENT_NAMES[agent_id]
    except KeyError:
        raise Refused(
            f"Unknown agent: {agent_id}. Iris knows "
            f"{', '.join(sorted(AGENT_NAMES))}.") from None


def config_path(agent_id: str, home: str) -> str:
    """The agent's config file path under the given home dir.

    Never the real home unless the caller passes it: tests pass a
    temp dir, the app passes the user's real home. Cursor's path
    differs by OS, so it follows the platform. The rest ride on
    the home dir on every OS.
    """
    _agent_name(agent_id)
    if agent_id == "claude-code":
        return os.path.join(home, ".claude", "settings.json")
    if agent_id == "continue":
        return os.path.join(home, ".continue", "config.yaml")
    if sys.platform == "win32":
        return os.path.join(
            home, "AppData", "Roaming", "Cursor", "User",
            "settings.json")
    if sys.platform == "darwin":
        return os.path.join(
            home, "Library", "Application Support", "Cursor",
            "User", "settings.json")
    return os.path.join(
        home, ".config", "Cursor", "User", "settings.json")


def read_config(agent_id: str, path: str) -> dict:
    """Parse the config file: YAML for Continue, JSON for the rest.

    Continue's file is YAML. JSON is valid YAML, so a
    JSON-written file parses fine too. The rest are plain JSON.
    """
    _agent_name(agent_id)
    with open(path, encoding="utf-8") as handle:
        if agent_id == "continue":
            config = yaml.safe_load(handle)
        else:
            config = json.load(handle)
    return config if isinstance(config, dict) else {}


def _read_bytes(path: str) -> bytes:
    """The file's raw bytes, for a byte-for-byte backup."""
    with open(path, "rb") as handle:
        return handle.read()


def _write_backup(path: str, original: bytes) -> str:
    """Save the original bytes beside the config, return the path."""
    backup_path = path + BACKUP_SUFFIX
    with open(backup_path, "wb") as handle:
        handle.write(original)
    return backup_path


def _claude_existing(env: dict) -> str | None:
    """The proxy Claude Code already routes through, if any."""
    return env.get("HTTP_PROXY") or env.get("HTTPS_PROXY") or None


def _cursor_existing(settings: dict) -> str | None:
    """The proxy Cursor already uses, if any."""
    return settings.get("http.proxy") or None


def _continue_existing(config: dict) -> str | None:
    """The first proxy a Continue model already uses, if any."""
    models = config.get("models") or []
    for model in models:
        if not isinstance(model, dict):
            continue
        options = model.get("requestOptions") or {}
        if options.get("proxy"):
            return options["proxy"]
    return None


def connect_agent(agent_id: str, proxy_url: str, home: str) -> str:
    """Point the agent at the Iris proxy. Return plain words.

    Back up first, write second, read back third. Refuse when the
    agent already has a proxy setting, when its config file is
    missing, or when the agent is unknown. A refusal changes
    nothing and makes no backup.
    """
    name = _agent_name(agent_id)
    path = config_path(agent_id, home)
    if not os.path.exists(path):
        raise Refused(
            f"{name} has no config file at {path}. "
            f"Open {name} once so it writes one, then connect again.")
    config = read_config(agent_id, path)
    if agent_id == "claude-code":
        existing = _claude_existing(config.get("env") or {})
    elif agent_id == "cursor":
        existing = _cursor_existing(config)
    else:
        existing = _continue_existing(config)
    if existing:
        raise Refused(
            f"{name} already routes through {existing}. "
            f"Iris changed nothing. Disconnect that proxy first, "
            f"then connect again.")
    original = _read_bytes(path)
    backup_path = _write_backup(path, original)
    if agent_id == "claude-code":
        env = config.get("env") or {}
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        env["NO_PROXY"] = env.get("NO_PROXY") or LOOPBACK_BYPASS
        config["env"] = env
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
    elif agent_id == "cursor":
        config["http.proxy"] = proxy_url
        config["http.proxySupport"] = "override"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
    else:
        for model in config.get("models") or []:
            if not isinstance(model, dict):
                continue
            options = model.get("requestOptions") or {}
            options["proxy"] = proxy_url
            options["noProxy"] = options.get("noProxy") or [
                "localhost", "127.0.0.1"]
            model["requestOptions"] = options
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
    read_back = read_config(agent_id, path)
    return (
        f"{name} now routes through {proxy_url}. "
        f"Updated {path}. Backup at {backup_path}. "
        f"Restart {name} so it picks up the change. "
        f"Read back: {read_back}.")


def disconnect_agent(agent_id: str, home: str) -> str:
    """Restore the backup. Return plain words.

    Refuse when there is no backup: the agent was never connected
    through Iris, so there is nothing to restore. The restore
    consumes the backup: the bytes go back, then the backup file
    goes away, so a second disconnect refuses instead of writing
    stale bytes.
    """
    name = _agent_name(agent_id)
    path = config_path(agent_id, home)
    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        raise Refused(
            f"{name} is not connected through Iris. "
            f"There is no backup to restore, so Iris changed nothing.")
    with open(backup_path, "rb") as handle:
        original = handle.read()
    with open(path, "wb") as handle:
        handle.write(original)
    os.remove(backup_path)
    return (
        f"{name} is disconnected. Restored {path} from the backup.")
