# Bridge watcher fix: stream Muse Code output, run in the repo directory

## Context

The Bridge watcher (`tools/bridge_watcher.py`) polls GitHub for new
prompt files and runs Muse Code headless on each one. Two bugs were
found in live use on the Dell, Nov 24, 2026. Both are in `run_muse`.
Both are real: the Connect prompt ran at 09:08:54, exited 0 in under
a minute, did no work, produced no output anywhere, and was marked
seen, so it will not run again on its own.

## Bug 3: run_muse swallows all Muse Code output

`run_muse` calls `subprocess.run` with `capture_output=True`. All
of Muse Code's stdout and stderr is captured into `proc.stdout` /
`proc.stderr`, and the function returns only `proc.returncode`.
The captured output is never used, never logged, never printed.
It is discarded. The watcher terminal shows nothing. There is no
record anywhere of what Muse Code did, printed, or why it exited.

Fix: remove `capture_output=True` so Muse Code's output streams to
the watcher terminal (inherited stdout/stderr). Keep `text=True`
only if it is still needed without capture; drop it if it is not.
The watcher terminal must show what Muse Code does, live.

## Bug 4: run_muse runs in the wrong directory

`run_muse` calls `subprocess.run` with no `cwd` argument, so Muse
Code runs in the watcher's working directory (`iris-bridge` on the
Desktop), not in the iris repo. The prompt tells Muse Code to
implement a feature in the iris repo, but there is no repo in
`iris-bridge`, so Muse Code cannot find the files, cannot
implement anything, and cannot push. It exited 0 in under a
minute, having done no work.

Fix: add a `repo_dir` config field to `bridge_watcher_config.json`
and pass `cwd=repo_dir` in `run_muse`.

- `repo_dir` is required. If it is missing or empty,
  `load_config` raises ConfigError in plain words naming the
  config file, and the watcher stops. This matches the watcher's
  existing rule: bad config stops the watcher with a plain-words
  error naming the file, instead of running the wrong prompts.
- A relative `repo_dir` resolves against the config file's own
  folder, the same way `state_file`, `log_file`, and
  `download_dir` already do.
- Charles will set `repo_dir` in the config to the local iris
  repo path on the Dell. Do not hardcode a path anywhere.
- Pass `cwd=repo_dir` to `subprocess.run` in `run_muse`.

## Tests first, then the fix

Write the regression tests first, in `tests/test_bridge_watcher.py`,
following the existing `RunMuseCommandTest` / `RunMuseShellTest`
mocking pattern (patch `subprocess.run`, patch `os.name` where
needed). Then implement the fix. Tests:

1. Output streams: `run_muse` calls `subprocess.run` without
   `capture_output` (so output inherits the terminal). Assert
   `capture_output` is not in the call kwargs.
2. Working directory: `run_muse` calls `subprocess.run` with
   `cwd` equal to the configured `repo_dir`. Assert the `cwd`
   kwarg matches.
3. Config requires `repo_dir`: `load_config` raises ConfigError
   naming the config file when `repo_dir` is missing or empty.
4. Relative `repo_dir` resolves against the config file's folder,
   the same as the other path fields.

The new tests must fail before the fix and pass after it. Run the
full suite (`python -m pytest tests/ -q`), `ruff check`, `flake8
--select=E9,F63,F7,F82`, and `pyright`. All must be green.

## Scope

- Change only `tools/bridge_watcher.py`, `tests/test_bridge_watcher.py`,
  and the config documentation (`tools/bridge_watcher_README.md`
  and the config file's own comments, if any) to describe `repo_dir`.
- Do not change the Connect prompt, the seen-list, the log format,
  or anything else.
- Do not merge, do not open a PR, do not touch main.

## Report and stop

Commit the tests separately from the fix, per repo rules. Push the
branch only. Report the commit hashes, the test results, and stop.
