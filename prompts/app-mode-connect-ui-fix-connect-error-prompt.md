# App-mode Connect: show plain words when connect fails unexpectedly (fix)

## Task

Fix one gap in the Connect flow, on branch app-mode/connect-ui, at
commit cf6db8b. The gap is in `appmode/shell.py`, `_on_connect`.

## The gap

`_on_connect` catches `Refused` from `_connect_fn`. It does not
catch any other exception. A generic exception from the connect
call (a disk error, a permission error, a broken config file)
escapes the click handler as a traceback. The user sees nothing.
In the packaged app there is no console, so the failure is silent.

The restart call has the same shape and is already fixed: it is
wrapped, and a failure shows plain words. Mirror that pattern
for the connect call.

## Fix

In `_on_connect`, wrap the `_connect_fn` call so a generic
exception shows plain words and returns, with no restart prompt.
Keep the `Refused` path as it is. The message names what happened
and what to do, in plain words, and never shows a traceback or
the whole config. Something like: "Connect did not run: {exc}.
The agent is not connected." Then return.

## Do not change

- Do not change the tests. They are the contract.
- Do not change the spec.
- Do not change `appmode/connect.py` or `appmode/restart.py`.

## Verify

- Write a throwaway probe (in $env:TEMP, not the repo) that
  makes `_connect_fn` raise a generic exception, clicks Connect,
  and checks the dialog shows plain words and no restart prompt
  appears. The probe is throwaway, do not commit it.
- Re-run the new test file, then the full suite with pytest.
  All must pass. Run ruff, flake8, and pyright on the changed
  file. All must pass.
- Commit the changed file only (named file, never `git add -A`).
  Push the branch `app-mode/connect-ui`. Do not open a pull
  request. Do not merge. Charles approves the merge in words.
- Report: the commit hash, the push result, the probe result,
  and the test results. Then stop.
