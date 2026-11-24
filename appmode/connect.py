"""One-click connect: write an agent's proxy setting to Iris.

Iris connects a known agent to the Iris proxy with one click. Iris
backs up the agent's config before it writes, shows the user what
changed in plain words, and restores the backup on disconnect.
If the agent already has a proxy setting, Iris refuses in plain
words and changes nothing. If the agent is not known, Iris says so
in plain words.

Agents in v1: Claude Code, Cursor, Continue, Muse Code. Each
stores its proxy in a plain file, so Iris edits the file. Config
paths take a home dir, so tests run under a temp home and never
touch a live config.

Loopback only. Metadata only. No bodies, no keys. Plain words only.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

import yaml

# The backup suffix: the original bytes, kept beside the config.
BACKUP_SUFFIX = ".iris-backup"

# Loopback bypass for a fresh proxy block. The agent must not send
# Iris's own traffic back through the proxy.
LOOPBACK_BYPASS = "localhost,127.0.0.1"

# Known agents: id to plain display name. The id is the file key.
# The name is what the human reads.
AGENT_NAMES = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "continue": "Continue",
    "muse-code": "Muse Code",
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
    if agent_id == "muse-code":
        return os.path.join(home, ".config", "muse", "settings.json")
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


def _parse_config(agent_id: str, text: str):
    """Parse raw config text: YAML for Continue, JSON for the rest.

    JSON is valid YAML, so a JSON-written Continue file parses
    fine too. Returns whatever the text holds: a value that is
    not a dict is broken, and connect refuses it instead of
    replacing it with a fresh dict.
    """
    _agent_name(agent_id)
    if agent_id == "continue":
        return yaml.safe_load(text)
    return json.loads(text)


def read_config(agent_id: str, path: str) -> object:
    """Parse the config file: YAML for Continue, JSON for the rest."""
    return _parse_config(agent_id, _read_bytes(path).decode("utf-8"))


def _check_proxy_url(proxy_url: str) -> str:
    """The proxy URL, cleaned, or refusal when it is not loopback.

    Iris is loopback only: scheme http or https, host 127.0.0.1
    or localhost. Anything else would send the agent's traffic
    off this machine, so Iris refuses and changes nothing. No
    backup, no write.
    """
    cleaned = (proxy_url or "").strip()
    parts = urlsplit(cleaned)
    if (parts.scheme not in ("http", "https")
            or (parts.hostname or "").lower()
            not in ("127.0.0.1", "localhost")):
        raise Refused(
            f"Iris only routes to this machine: {proxy_url} is "
            f"not a loopback URL with scheme http or https. "
            f"Iris changed nothing.")
    return cleaned


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


def _with_loopback_str(value: str | None) -> str:
    """A comma bypass list with localhost and 127.0.0.1 in it.

    Keeps the user's entries, appends what is missing, never
    duplicates what is there.
    """
    seen = [part.strip() for part in (value or "").split(",")]
    seen = [part for part in seen if part]
    for host in ("localhost", "127.0.0.1"):
        if host not in seen:
            seen.append(host)
    return ",".join(seen)


def _with_loopback_list(value) -> list:
    """A bypass list with localhost and 127.0.0.1 in it.

    Same promise as the string form: keep, append, never
    duplicate. The list shape stays a list.
    """
    seen = [str(item).strip() for item in (value or [])]
    seen = [item for item in seen if item]
    for host in ("localhost", "127.0.0.1"):
        if host not in seen:
            seen.append(host)
    return seen


def _claude_existing(env: dict) -> str | None:
    """The proxy Claude Code already routes through, if any."""
    return env.get("HTTP_PROXY") or env.get("HTTPS_PROXY") or None


def _cursor_existing(settings: dict) -> str | None:
    """The proxy Cursor already uses, if any."""
    return settings.get("http.proxy") or None


def _muse_existing(settings: dict) -> str | None:
    """The proxy Muse Code already uses, if any."""
    transport = settings.get("endpoint_transport") or {}
    if not isinstance(transport, dict):
        return None
    return transport.get("proxy") or None


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

    Read first, re-read just before writing. Refuse when the file
    changed mid-connect, when it holds no dict, when Continue
    holds no models, when a proxy is already set, when the file
    is missing, or when the agent is unknown. A refusal changes
    nothing and makes no backup. The result names the proxy
    fields only, never the whole config, so no secret leaks.
    """
    name = _agent_name(agent_id)
    proxy_url = _check_proxy_url(proxy_url)
    path = config_path(agent_id, home)
    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(path):
        raise Refused(
            f"{name} has no config file at {path}. "
            f"Open {name} once so it writes one, then connect again.")
    original = _read_bytes(path)
    config = _parse_config(agent_id, original.decode("utf-8"))
    if not isinstance(config, dict):
        raise Refused(
            f"{name} config at {path} is not a settings file "
            f"Iris can read. Iris changed nothing.")
    if agent_id == "continue":
        models = config.get("models")
        if not isinstance(models, list) or not models:
            raise Refused(
                f"{name} has no models in {path}. "
                f"Iris changed nothing.")
    if agent_id == "muse-code":
        if config.get("schema_version") != 1:
            raise Refused(
                f"{name} config at {path} needs schema_version 1. "
                f"Iris changed nothing.")
        transport = config.get("endpoint_transport")
        if transport is not None and not isinstance(transport, dict):
            raise Refused(
                f"{name} config at {path} has an endpoint_transport "
                f"block Iris cannot read. Iris changed nothing.")
    fresh = _read_bytes(path)
    if fresh != original:
        raise Refused(
            f"{name} config at {path} changed while Iris was "
            f"connecting. Iris kept the agent's change and "
            f"wrote nothing.")
    if os.path.exists(backup_path):
        raise Refused(
            f"{name} is already connected: a backup is waiting "
            f"at {backup_path}. Disconnect first to restore it, "
            f"or delete the backup, then connect again.")
    if agent_id == "claude-code":
        existing = _claude_existing(config.get("env") or {})
    elif agent_id == "cursor":
        existing = _cursor_existing(config)
    elif agent_id == "muse-code":
        existing = _muse_existing(config)
    else:
        existing = _continue_existing(config)
    if existing:
        raise Refused(
            f"{name} already routes through {existing}. "
            f"Iris changed nothing. Disconnect that proxy first, "
            f"then connect again.")
    backup_path = _write_backup(path, original)
    note = ""
    if agent_id == "claude-code":
        env = config.get("env") or {}
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        env["NO_PROXY"] = _with_loopback_str(env.get("NO_PROXY"))
        config["env"] = env
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        change = f"HTTP_PROXY and HTTPS_PROXY to {proxy_url}"
    elif agent_id == "cursor":
        config["http.proxy"] = proxy_url
        config["http.proxySupport"] = "override"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        change = f"http.proxy and http.proxySupport to {proxy_url}"
        note = (" Note: override sends Cursor updates and "
                "extension downloads through Iris too, and Iris "
                "blocks destinations it does not allow.")
    elif agent_id == "muse-code":
        transport = config.get("endpoint_transport") or {}
        transport["proxy"] = proxy_url
        config["endpoint_transport"] = transport
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        change = f"endpoint_transport.proxy to {proxy_url}"
    else:
        for model in config["models"]:
            if not isinstance(model, dict):
                continue
            options = model.get("requestOptions") or {}
            options["proxy"] = proxy_url
            bypass = options.get("noProxy")
            if isinstance(bypass, str):
                options["noProxy"] = _with_loopback_str(bypass)
            elif isinstance(bypass, list):
                options["noProxy"] = _with_loopback_list(bypass)
            else:
                options["noProxy"] = ["localhost", "127.0.0.1"]
            model["requestOptions"] = options
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
        count = sum(
            1 for model in config["models"]
            if isinstance(model, dict))
        change = (f"requestOptions.proxy to {proxy_url} "
                  f"on {count} model(s)")
    _read_back = read_config(agent_id, path)
    return (
        f"{name} now routes through {proxy_url}. Set {change} "
        f"in {path}. Backup at {backup_path}. Restart {name} "
        f"so it picks up the change.{note}")


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
