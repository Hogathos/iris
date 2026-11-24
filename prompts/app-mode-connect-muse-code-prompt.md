# App mode: Connect Muse Code — full-context Bridge prompt

Implement Muse Code Connect from the spec. Read the spec first,
then the rules, then the code. The tests are already written and
failing. Do not edit them. Make them pass.

## Read

- `specs/app-mode-connect-muse-code/requirements.md` — the spec.
  It is the success criteria.
- `tests/test_appmode_connect_muse_code.py` — the 16 failing
  tests. They are the contract. Make them pass. Do not edit them.
- `appmode/connect.py` — the connect module. It already handles
  Claude Code, Cursor, and Continue. Add Muse Code the same way.
- `appmode/restart.py` — the restart module. Add a Muse Code
  branch to `_manual_instruction` so it does not fall through to
  the Cursor wording.
- `AGENTS.md` — the working rules.

## Do

1. In `appmode/connect.py`, add `"muse-code": "Muse Code"` to
   AGENT_NAMES. Add its config path: `~/.config/muse/settings.json`
   under the home dir the caller passes, on every OS. Add the
   proxy read and write: `endpoint_transport.proxy` in that file,
   set to the Iris proxy URL as a string. Refuse in plain words
   when a proxy is already set, when the file is missing, when it
   is not a dict, when schema_version is missing or not 1, and
   when a backup already waits. Keep every other key. Back up
   before writing. Read the file back after writing. Disconnect
   restores the backup byte for byte and removes it.
2. In `appmode/restart.py`, add a Muse Code branch to
   `_manual_instruction`: "Muse Code runs in your terminal. Quit
   this session and start a new one to pick up the proxy change."
3. Run the new tests (`python -m pytest
   tests/test_appmode_connect_muse_code.py -q`). All 16 must pass.
4. Run the full suite (`python -m pytest tests/ -q`) plus
   `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`, and pyright.
   All must pass. A connect change must not break the suite.
5. Run the oracle, a live test on this machine: connect Muse
   Code through the Iris app, run a prompt in `muse`, confirm the
   traffic reaches Iris's proxy and the journal records it, then
   disconnect and confirm the settings file is back byte for byte.
   Report the result. If the proxy value shape is not a string
   URL (if `muse` does not accept it), record the shape that
   works in the spec's Decisions and adjust the tests, and say
   so in the report.
6. Report the commit hash, the test results, the oracle result,
   and what is still open. Then stop.

## Rules

- Do not edit the tests. They are the contract. If a test is
  wrong, say so in the report and stop; do not fix it by editing
  it.
- Commit named files only. Never `git add -A`, never
  `git commit -a`. Never commit `.exe`, `dist/`, `build/`,
  `*.db`, `.pytest_cache/`, or secrets.
- Push the branch only. Do NOT open a PR or merge.
- The result never carries a secret. The settings file can hold
  mcp_servers entries with headers and tokens. The result names
  the proxy only, never the whole config.
- Do not report session usage; the usage panel is not readable
  from inside the session, Charles screenshots it.
