# Muse Code prompt — one-click connect review-pass fixes (risks 7, 9, 8, in that order)

Read this whole file, then do it, report, stop.

## Task

Fix the three remaining review-pass risks in `appmode/connect.py`
so the four new failing tests pass. The tests are the contract.
Do not change the test file or the spec.

Branch: `app-mode/connect`. Head is `b3dc3ef` (four failing tests
added on top of the risk 1–5 fix at `9b83d1d`).

## Read first, in this order

1. `specs/app-mode-connect/requirements.md` — the spec.
2. `tests/test_appmode_connect.py` — the contract. The four new
   tests are at the end of `ConnectTest`. They fail against the
   current code. They must pass after your fix. The fifteen
   existing tests must still pass.
3. The review pass, `one-click-connect-review-pass.md`, in the
   goal workspace (Main has it; it names the risks). If you cannot
   see it, the fixes below carry the same content.

## The three fixes, in this order

1. **Backup already exists (risk 7).** A backup means a previous
   connect was never disconnected. Refuse instead of overwriting
   the backup with a new one, so disconnect still restores the
   original state. Raise `Refused` in plain words, naming the
   backup, and tell the user to disconnect first or delete the
   backup. Change nothing, make no new backup. The check goes
   before the backup is written. The test
   `test_connect_refuses_when_backup_already_exists` connects,
   removes the proxy by hand, connects again, and asserts the
   refusal, the file unchanged, and the original backup intact.
2. **Proxy URL validation (risk 9).** Iris is loopback only.
   Validate `proxy_url` before writing it into a config. It must
   parse as a URL with scheme http or https and host 127.0.0.1
   or localhost. Anything else, including a string that is not a
   URL at all, raises `Refused` in plain words. Change nothing,
   make no backup. The tests
   `test_connect_refuses_non_loopback_proxy_url` and
   `test_connect_refuses_malformed_proxy_url` assert the refusal,
   the file unchanged, and no backup. The normal loopback URL
   (`http://127.0.0.1:8888`) already works and must keep working.
3. **Cursor override note (risk 8).** Cursor's http.proxySupport
   "override" sends updates and extension downloads through Iris
   too, and Iris blocks non-allowlisted destinations. Add one
   line to the Cursor connect result, in plain words, saying so,
   so the user is not surprised. Only the Cursor result changes.
   The test `test_cursor_connect_notes_override_side_effect`
   asserts the result mentions updates.

## What NOT to fix here

- Risk 6 (YAML reformat) is accepted, no fix. The backup is the
  safety net. Do not hand-roll a YAML parser.
- Do not change the test file. Do not change the spec.
- Do not touch risks 1–5. They are fixed and their tests pass.

## Checks

- Run both runners: `python -m unittest discover -s tests` and
  `python -m pytest tests/ -q`. Both must pass. The suite is 19
  tests in `test_appmode_connect.py` (15 existing + 4 new) and 258
  total (254 + 4). Do not quote a test count from memory. Run the
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
