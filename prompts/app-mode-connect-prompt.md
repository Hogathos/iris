# Muse Code prompt: implement one-click connect (appmode/connect.py)

Branch: `app-mode/connect`. The failing test and the spec are
already on the branch (commit `7d94442`).

## Task

Implement `appmode/connect.py` so all 9 tests in
`tests/test_appmode_connect.py` pass. Do not change the test.
Do not change the spec.

## Read first, in this order

1. `specs/app-mode-connect/requirements.md` — the spec. What it
   does, the success criteria, the agents in v1, the flow, the
   non-goals, the decisions.
2. `tests/test_appmode_connect.py` — the contract. It names the
   module API: `config_path(agent_id, home)`,
   `connect_agent(agent_id, proxy_url, home)`,
   `disconnect_agent(agent_id, home)`,
   `read_config(agent_id, path)`, and the `Refused` exception.
   The test uses a temp home dir, never the real one.
3. `appmode/shell.py` — the existing appmode module, for style
   and for how the package is laid out. Match its voice: short
   sentences, one idea per sentence, active voice, no em dash.
   Keep verbose comments and docstrings.

## Agents in v1

- Claude Code: `~/.claude/settings.json`, proxy in the `env`
  block (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`).
- Cursor: user `settings.json`, `http.proxy` plus
  `http.proxySupport: "override"`. Full quit and reopen.
- Continue: `~/.continue/config.yaml`, per-model
  `requestOptions.proxy` plus `requestOptions.noProxy`.

Config paths differ by OS. `config_path` takes a home dir and
returns a path inside it, on all three OSes. Never touch the
real home dir in tests. Never touch a live config.

## The one new dependency

Continue's config is YAML. Add `PyYAML` to `requirements.txt`
and use it (`yaml.safe_load`, `yaml.safe_dump`). JSON is valid
YAML, so the test's JSON-written Continue config parses fine.

Known risk, name it in your report: `safe_dump` reformats the
file (it does not preserve comments or the user's formatting).
The backup is the safety net. Do not hand-roll a YAML parser.

## Rules

- The test is the check. Make it pass. Do not weaken it.
- Run both runners: `python -m unittest discover -s tests -v`
  and `python -m pytest tests/ -q`. Both must pass.
- Lint clean: `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`,
  pyright with `--pythonpath` to the venv python. Zero errors.
- Keep verbose comments and docstrings. Do not strip them.
- Stage named files only. Never `git add -A`. Never
  `git commit -a`. Never commit `.exe`, `dist/`, `build/`,
  `attempts.db`, `*.db`, `.pytest_cache/`, `*.spec`, secrets.
- Commit and push to `app-mode/connect` only. Do NOT merge.
  Do NOT open a pull request. Charles approves merges in words
  in Main.
- Report: commit hash, push result, test results (both
  runners), lint results, and what is still open. Then stop.
