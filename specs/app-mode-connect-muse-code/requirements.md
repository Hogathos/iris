# App mode: Connect Muse Code — requirements

## What it does

Iris connects Muse Code to the Iris proxy with one click, the
same as the other agents. Muse Code is Meta's `muse` coding
agent CLI. Charles is the first user and he uses it in PyCharm,
so it belongs in the Connect dialog. The v1 three (Claude Code,
Cursor, Continue) came from consumer research and missed him.

## How it works

- Muse Code keeps its settings at `~/.config/muse/settings.json`
  on every OS. The file requires `"schema_version": 1`.
- Muse Code has its own proxy setting inside that file:
  `endpoint_transport.proxy`. Iris sets it to the Iris proxy URL.
  The value is the proxy URL as a string, the same shape as
  Cursor's `http.proxy`. The implementer confirms the shape by a
  live test against the real `muse` CLI (Charles has it, v1.4.25).
  If the shape differs, the implementer records it here and
  adjusts the tests.
- Iris backs up the settings file before it writes, shows the
  user what changed in plain words, and restores the backup on
  disconnect. If Muse Code already has a proxy setting, Iris
  refuses in plain words and changes nothing. If the file is
  missing, Iris refuses in plain words. If the file is not a
  dict, or its schema version is missing or not 1, Iris refuses
  in plain words and changes nothing.
- Muse Code reads settings at startup, so open sessions need a
  restart. Muse Code is a terminal CLI, so Iris cannot restart
  it the way it restarts Cursor. The restart returns the manual
  instruction, the same shape as Claude Code: quit the session
  and start a new one. The restart module gains a Muse Code
  branch, so it does not fall through to the Cursor wording.
- The Connect dialog picks the new agent up from AGENT_NAMES
  with no UI change. The dialog already lists every agent in
  AGENT_NAMES.

## Success criteria

- Connect writes `endpoint_transport.proxy` to the Iris proxy
  URL, backs up the original file byte for byte, and returns
  plain words naming Muse Code, the file, and the proxy URL.
- Connect preserves every other key in the file: schema_version,
  model, mcp_servers, and anything else the user set.
- Connect refuses when a proxy is already set, when the file is
  missing, when it is not a dict, when schema_version is missing
  or not 1, and when a backup already waits. Each refusal changes
  nothing and makes no backup.
- Disconnect restores the backup byte for byte and removes the
  backup file. A second disconnect refuses.
- The result never carries a secret. The settings file can hold
  mcp_servers entries with headers and tokens. The result names
  the proxy only, never the whole config.
- Restart returns the Muse Code manual instruction, not the
  Cursor wording.
- The oracle: a live test on the Dell. Connect Muse Code, run a
  prompt, confirm the traffic reaches Iris's proxy and the
  journal records it, then disconnect and confirm the settings
  file is back byte for byte. The implementer runs this and
  reports it.

## Non-goals

- No gateway mode. Iris stays a forward proxy. Muse Code's
  traffic goes through Iris by the proxy setting, not by
  pointing its endpoint at Iris.
- No change to how Iris proxies Claude Code, Cursor, or
  Continue. No change to the Connect dialog layout.
- No automatic restart of Muse Code. The manual instruction is
  the restart.

## Decisions

- Mechanism is `endpoint_transport.proxy` in settings.json, not
  env vars and not endpoint_transport.base_url. Evidence: the
  Muse Spark ACP docs state Muse Code reads HTTPS_PROXY,
  HTTP_PROXY, ALL_PROXY and NO_PROXY itself and has its own
  endpoint_transport.proxy setting. The oh-my-musecode
  reverse-engineering report confirms endpoint_transport
  supports a proxy field. Env vars would need a shell or a
  wrapper; base_url would make Iris a gateway. The proxy field
  is a config-file edit, the same shape as the other three
  agents. (Charles, Nov 24, 2026; research Nov 24, 2026.)
- Config path is `~/.config/muse/settings.json` on every OS,
  under the home dir the caller passes. Tests use a temp home.
  (Charles, Nov 24, 2026.)
- Restart is the manual instruction, not a real restart. Muse
  Code is a terminal CLI. Iris cannot restart it the way it
  restarts Cursor. The restart module gains a Muse Code branch
  so it does not fall through to the Cursor wording.
  (Charles, Nov 24, 2026.)
- Files changed: `appmode/connect.py` (the agent, the path, the
  proxy read and write) and `appmode/restart.py` (the manual
  instruction branch). No other file.
