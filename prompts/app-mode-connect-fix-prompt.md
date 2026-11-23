# Muse Code prompt — one-click connect review-pass fixes (risks 1, 2, 3, 4, 5)

Read this whole file, then do it, report, stop.

## Task

Fix the five review-pass risks in `appmode/connect.py` so the six
new failing tests pass. The tests are the contract. Do not change
the test file or the spec.

Branch: `app-mode/connect`. Head is `82ba399` (six failing tests
added on top of the implementation at `9389a45`).

## Read first, in this order

1. `specs/app-mode-connect/requirements.md` — the spec.
2. `tests/test_appmode_connect.py` — the contract. The six new
   tests are at the end of `ConnectTest`. They fail against the
   current code. They must pass after your fix. The nine original
   tests must still pass.
3. The review pass, `one-click-connect-review-pass.md`, in the
   goal workspace (Main has it; it names the risks). If you cannot
   see it, the fixes below carry the same content.

## The five fixes

1. **Secret leak in the read-back (risk 1).** `connect_agent`
   returns the full parsed config in the `Read back:` string.
   Continue's config holds a model `apiKey` per model. Claude
   Code's `env` block can hold API keys. The return value must
   never carry a secret. Change the read-back so it names the
   proxy fields only, never the whole config. The test
   `test_connect_result_does_not_leak_secrets` asserts the
   apiKey `sk-test-only` is absent from the result and the proxy
   URL is present.
2. **False success when Continue has no models (risk 2).**
   When `models` is empty or missing, no proxy is written, but
   the function reports success. Refuse instead. Raise `Refused`
   in plain words, change nothing, make no backup. The test
   `test_continue_connect_refuses_when_no_models` asserts the
   refusal, the file unchanged, and no backup file.
3. **Config changes mid-connect (risk 3).** If the config file
   changes while Iris is connecting (the agent rewrote it), Iris
   must refuse instead of writing over the agent's change with
   stale data. Re-read the file bytes just before writing, before
   the backup is made, and refuse when they differ from what was
   read at the start. The refusal is plain words and names the
   change. The test
   `test_connect_refuses_when_config_changes_mid_connect` patches
   `_read_bytes` so the file changes after the first read; it
   asserts the refusal, the agent's change intact, and no backup.
   The re-read must use the same read path the module already
   uses (`_read_bytes`), so the test's patch is the seam.
4. **NO_PROXY / noProxy append (risk 4).** An existing NO_PROXY
   (Claude Code) or noProxy list (Continue) is kept as-is today.
   When it lacks localhost and 127.0.0.1, append them. Keep the
   user's existing entries. Do not duplicate entries already
   present. The tests
   `test_claude_connect_appends_loopback_to_no_proxy` and
   `test_continue_connect_appends_loopback_to_no_proxy` assert
   the existing entry is kept and localhost and 127.0.0.1 are
   present.
5. **Non-dict config (risk 5).** A config that parses but is not
   a dict is broken. Refuse instead of replacing it with a fresh
   dict. Raise `Refused` in plain words, change nothing, make no
   backup. The test `test_connect_refuses_non_dict_config`
   asserts the refusal, the file unchanged, and no backup.

## What NOT to fix here

- Risk 6 (YAML reformat) is accepted, no fix. The backup is the
  safety net. Do not hand-roll a YAML parser.
- Risk 7 (backup overwrite), risk 8 (Cursor override side
  effect), and risk 9 (proxy_url validation) are not in this
  batch. Do not touch them.
- Do not change the test file. Do not change the spec.

## Checks

- Run both runners: `python -m unittest discover -s tests` and
  `python -m pytest tests/ -q`. Both must pass. The suite is 15
  tests in `test_appmode_connect.py` (9 original + 6 new) and 254
  total (248 + 6). Do not quote a test count from memory. Run the
  suite and read the result.
- Ruff clean, flake8 (`--select=E9,F63,F7,F82`) clean, pyright
  0 errors on the changed module.
- Keep verbose comments and docstrings. A human found or wrote
  them. They are part of the code, not slop.
- The module writes to a temp home in tests, never the real one.
  Keep that true.

## Git

- Stage named files only. Never `git add -A`. Never
  `git commit -a`.
- Never commit `.exe`, `dist/`, `build/`, `attempts.db`, `*.db`,
  `.pytest_cache/`, `*.spec`, or secrets.
- Commit and push to branch `app-mode/connect` only.
- Do NOT merge. Do NOT open a pull request.
- Report the commit hash, the push result, and the test results.
  Then stop.
