# Bridge watcher fix: run_muse must launch muse.cmd on Windows — fix prompt

Fix the bug that stops the watcher from running any prompt on the
Dell. Read the report first, then the code, then fix it. Add
regression tests that fail before the fix and pass after it. Do not
edit the existing tests. Make the new tests pass and the full suite
green.

## Bug: run_muse cannot launch muse.cmd on Windows

On the Dell, the watcher runs but every poll fails with:
`bridge watcher: poll failed ([WinError 2] The system cannot find
the file specified), retrying`

Root cause, confirmed by Main from the live `where.exe muse`
output: Muse Code is installed as a `.cmd` file, not an `.exe`:
`C:\Users\Quant\AppData\Local\Programs\muse\muse.cmd`
PowerShell runs `.cmd` files fine. But `run_muse` in
`tools/bridge_watcher.py` calls `subprocess.run(command, ...)`
with the default `shell=False`. On Windows, `subprocess` uses
CreateProcess, which cannot run `.cmd` or `.bat` files directly,
so it raises FileNotFoundError ([WinError 2]). The error is not
caught inside `run_once` (the runner call has no try/except), so
it reaches the poll loop, which logs "poll failed" and retries
forever without ever running a prompt.

The baseline run at 7:54 AM succeeded because baselining never
calls the runner. The first real poll, which tries to run the
Muse Code Connect prompt, is the one that fails.

## Fix

In `run_muse` in `tools/bridge_watcher.py`, pass
`shell=(os.name == "nt")` to `subprocess.run`. On Windows this
runs the command through cmd.exe, which can launch `.cmd` files.
On other platforms `os.name` is not `"nt"`, so `shell` is False
and behaviour is unchanged. Change nothing else in `run_muse`.

## Regression tests

Add tests in `tests/test_bridge_watcher.py` that fail before the
fix and pass after it. Do not edit the existing tests. Follow the
pattern of `RunMuseCommandTest`: mock `watcher.subprocess.run`
with `mock.patch.object` and capture the kwargs it is called
with.

- On Windows: patch `os.name` to `"nt"` (with
  `mock.patch.object(watcher.os, "name", "nt")`), call
  `run_muse` with a command list, assert `subprocess.run` was
  called with `shell=True`.
- On other platforms: patch `os.name` to `"posix"`, call
  `run_muse`, assert `subprocess.run` was called with
  `shell=False`.

Patching `os.name` makes the tests run the same on any platform,
so they fail before the fix and pass after it wherever they run.

## Read

- `tools/bridge_watcher.py` — the watcher. The bug is in
  `run_muse` only.
- `tests/test_bridge_watcher.py` — the existing tests, including
  `RunMuseCommandTest` for the mocking pattern. Do not edit them.
- `AGENTS.md` — the working rules.

## Do

1. Add the regression tests first. Run them. They must fail:
   `run_muse` does not pass `shell` today, so the Windows test
   gets `shell` absent instead of True.
2. Fix `run_muse` as above.
3. Run the new tests. They must pass.
4. Run the full suite (`python -m pytest tests/ -q`). It must be
   green. Run ruff and flake8 as in AGENTS.md. Report the commit
   hash, the test results, and stop. Do not merge, do not open a
   PR.
