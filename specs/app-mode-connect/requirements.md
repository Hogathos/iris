# One-click connect

## What it does

Iris connects a known agent to the Iris proxy with one click. Iris
writes the agent's proxy setting so the agent's traffic flows through
Iris's loopback proxy. Iris backs up the agent's config before it
writes, shows the user what changed, and restores the backup on
disconnect or clean exit.

## Success criteria

- The user picks an agent from the list. Iris backs up the agent's
  config file. Iris writes the proxy setting (Iris loopback
  address). Iris reads the file back and shows the user the change
  in plain words.
- The user restarts the agent. The agent's traffic appears in Iris.
- One click disconnects. Iris restores the backup and shows the user
  the restore in plain words.
- If the agent already has a proxy setting, Iris refuses in plain
  words and changes nothing.
- If the agent is not in the known list, Iris says so in plain words.

## Agents in v1

Claude Code, Cursor, Continue. These have a clean config-file path
for the proxy setting. Research: `one-click-connect-agent-config-research.md`.

- Claude Code: `~/.claude/settings.json`, proxy in the `env` block
  (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`).
- Cursor: user `settings.json`, `http.proxy` plus
  `http.proxySupport: "override"`. Full quit and reopen to pick it up.
- Continue: `~/.continue/config.yaml`, per-model
  `requestOptions.proxy` plus `requestOptions.noProxy`.

## Flow

Pick the agent. Iris finds its config file. Iris backs up the file.
Iris writes the proxy setting. Iris reads the file back. Iris shows
the user the change. Iris offers to restart the agent for the user,
in one click, after a warning and a check for unsaved work. The
user says yes, the agent restarts, and traffic flows. Disconnect
restores the backup.

Every agent needs a restart to pick up a proxy change. No agent
hot-reloads proxy settings. The restart is part of the flow. For
a CLI agent, the restart is a new session. For a GUI agent, Iris
restarts it, but only after it warns the user and checks for
unsaved work. Nothing is lost.

## Non-goals

- ICP. That is the long game.
- Custom harnesses beyond an env-var fallback.
- Serving an API. Iris is a forward proxy, not a model endpoint.
  A base URL pointed at Iris would 404. The proxy route is the
  mechanism, not the base URL.
- Cline. Its model config lives in SQLite (`state.vscdb`), not a
  file, so Iris cannot write it by editing a file.
- Windsurf, until its manual proxy key is confirmed.
- Env-var-only agents in v1 (Aider, Codex CLI, Gemini CLI, Zed,
  OpenCode). Their proxy works only through env vars, which Iris
  cannot write into a config file. A later step covers them.

## Future hedge: content inspection

Content inspection is not v1, but it is a hedge worth keeping.
An agent that wants to outsmart Iris can hide what it does in the
request body, where metadata cannot see it. A secret exfiltrated
in a prompt, or an injected instruction, does not show in the
destination, the method, or the byte count. Content inspection
catches that.

It needs TLS termination to read the encrypted bodies. The shape,
when it is built: the user opts in, local only, and the user
chooses what Iris keeps. The default keeps no bodies and logs
only the verdict. If the user wants the extra information, the
decrypted content is available to them. It is their data, on
their machine, from their agent. Prompts are not kept by
default. Opt-in is the default for security features. Anything
that reads more than metadata needs the user's explicit choice.
The default stays metadata-only.

## Decisions

- 2026-11-22, Charles: one-click connect now, ICP as the long game.
  Chosen: one-click connect. Rejected: ICP first. Why: ICP needs
  adoption; one-click connect works with the agents people already use.
- 2026-11-22, Charles: popular agents only. Chosen: the known list.
  Rejected: any agent. Why: each agent stores its proxy in a
  different place, and Iris must know the exact file and key.
- 2026-11-22, Charles: never overwrite an existing proxy setting
  blind. Chosen: refuse in plain words and change nothing. Rejected:
  overwrite and keep the old value in memory only. Why: the user
  may route through another proxy on purpose, and silent overwrite
  would break it.
- 2026-11-22, Charles: backup before, read-back after, show the
  user, undo. Chosen: a backup file plus a read-back plus a plain
  words diff plus a one-click restore. Rejected: write without a
  backup. Why: the config is the user's, and a bad write must be
  reversible in one click.
- 2026-11-23, research: proxy route, not base URL. Chosen: write
  the proxy setting. Rejected: write a base URL pointing at Iris.
  Why: Iris does not serve API paths, so a base URL would 404.
- 2026-11-23, Charles: Iris restarts the agent for the user.
  Chosen: one click, after a warning and a check for unsaved
  work. Rejected: the user restarts by hand. Why: the restart is
  friction, and Iris can do it safely if it checks first and
  warns. Nothing is lost.
