# Bridge watcher: fix the Windows-only run-status test race (PR #67)

## Context

PR #67 ("Bridge watcher: run-status reporting for Muse and Codex",
branch `bridge/watcher-run-status`, head `f5eead6`) has one CI
failure, Windows only. Everything else passed: macOS 3.12,
Ubuntu 3.10 / 3.12 / 3.14, CodeQL. The failing test is
`tests/test_bridge_watcher.py::test_run_status_codex_publishes_live_output_and_finishes_failed`
(at `tests/test_bridge_watcher.py:2644`). Overall on Windows:
1 failed, 454 passed, 51 subtests passed.

The failing assertion:

```
assert git(origin, "ls-tree", commits[-2], "--", report_path) == ""
```

Error: the report file `prompts/progress-codex-report.md` was
already present in the second-to-last commit touching the
status file. The test expects the report to first appear only
in the fake agent's own final commit ("Add agent report", exit
code 7).

All the content assertions in the same test passed: the live
snapshots showed "State: running", the same "Started"
timestamp, "output line 075" and "last stderr line" present,
1-50 output lines, and increasing "Last updated" timestamps.
The live-output publishing and failed-finish behavior the test
is really checking worked. The identical test passed on macOS
and all three Ubuntu runners. This is a timing race, not a
functional failure of the run-status code.

## Working theory of the race (verify it, do not assume it)

The fake agent in `_run_status_round_trip` prints 75 lines plus
a stderr line, waits for two timed snapshots at the origin
while silent (STATUS_INTERVAL_SECONDS mocked to 0.75s), then
writes the report, runs `git add` and `git commit --only -m
"Add agent report" -- <report>`, and exits with code 7.

The watcher snapshots on a timer and uses a path-only commit
(`git add --intent-to-add` then `git commit --only -m "Bridge
run status..." -- <status_path>`). A `--only` commit builds on
the parent tree, so if a timed snapshot fires after the agent
has committed the report but before the final snapshot, that
status commit's tree contains the report (inherited from the
parent), even though the status commit itself only changed the
status file. On Windows, git operations are slower, widening
the window for a timed snapshot to land in that gap.

Verify this theory against the code before fixing. In
particular, check whether the report can land in a status
commit's tree through the parent, and whether the timer can
fire between the agent's report commit and the agent's exit.

## What to do

Fix the race so the test is not timing-sensitive on Windows.
The report appearing in a status commit's tree one commit
early is harmless in practice (the report is meant to be
published anyway), so prefer the smallest fix that makes the
test robust: either make the test's commit-ordering assertion
tolerant of a snapshot that lands after the agent's report
commit, or stop the timer promptly enough that no timed
snapshot fires after the agent's report commit. Do not weaken
the assertions that matter: the live snapshots must still show
running state with the 50-line tail, the final state must still
be `failed` for exit code 7, the agent's staged `unrelated.txt`
must still be preserved, and the report must still reach the
origin.

Do not change the run-status behavior, the routing, the
seen-list rules, the report names, or the hooks. The approved
tests, the implementation, and the spec stay as they are except
for what this race requires.

## Verification

- Run the two origin tests (`test_run_status_muse_...` and
  `test_run_status_codex_...`) repeatedly (at least 5 times
  each) to shake out the race, then run the full suite.
- Run Ruff lint and the watcher's format check, and Pyright.
- Run the demo scripts if the change touches the watcher.
- The fix must pass on Windows timing, not just on the faster
  runners: if you cannot run Windows locally, make the test
  deterministic rather than timing-dependent, and say so in
  the report.

## Report

Write the report to
`prompts/bridge-watcher-run-status-windows-race-codex-report.md`.
Record what the race was (verified, not assumed), what you
changed and why, the repeat-run counts, the full suite result,
and the lint result. Verify the code changes and the report
file exist in the working tree before finishing. Do not stop
for approval. The commit, GitHub push, and CI check remain
with Charles.

## House rules

Challenge the frame: before non-trivial work, if a simpler or
sturdier way exists, say so once, up front, then do what this
prompt asks. Do not write Charles's assignments or assessed
coursework. Use `activate.bat` or call
`.\.venv314\Scripts\python.exe` directly (Activate.ps1 fails
in a non-interactive session).
