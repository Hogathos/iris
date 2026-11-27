# Bridge watcher run status, re-run

## Status

The failing tests are on disk in `tests/test_bridge_watcher.py`. The watcher implementation awaits Charles's review of those tests. `AGENTS.md` requires that review before implementing new behavior.

| Requested behavior | Test and current result |
| --- | --- |
| Write a local running status with the agent, prompt name, UTC timestamps, and recent output. | Both route tests fail because the child process finds the status file missing at launch. The tests also check the 50-line tail and terminal output. |
| Push every two minutes, even when the agent is silent. | `test_run_status_uses_a_two_minute_timer` fails because the timer constant is absent. Both route tests require two timed snapshots at a local Git origin while the child is silent. |
| Publish final status with the report and leave the status file on disk. | Both route tests check the final local and remote status, the report at the same remote branch tip, and preservation of a separately staged file. These assertions await implementation. |
| Support both routes through the watcher. | `test_run_status_muse_publishes_live_output_and_finishes_done` exercises Muse with exit 0. `test_run_status_codex_publishes_live_output_and_finishes_failed` exercises Codex with exit 7. Status reporting for both routes remains unimplemented. |

## Checks

- Focused pytest run had 3 expected failures and 95 deselected tests.
- The full pytest run had 434 passed, 3 failed, and 46 passed subtests in 210.97 seconds. The failures were the three new tests. Pytest also emitted two cache warnings.
- `ruff check .` passed. Pyright reported 0 errors and 0 warnings.
- Ruff's format check passed for the added test block. The full-file format check flagged the watcher and test file before any edits. The pre-commit hook checks whole files.
- Both demo scripts ran against a temporary seeded database. They reported 6 attempts, 3 traces, 2 successes, and 3 retries. The visualizer reported a recorded demo cost of $0.0120.
- A scan of the added text found zero matches for common credential patterns.

## Proposed implementation

1. Review the failing tests in `tests/test_bridge_watcher.py`.
2. Add the shared status flow to `tools/bridge_watcher.py`. Write the first snapshot at launch. Forward output to the terminal as it arrives and keep a bounded tail. Use a two-minute timer that runs during silence. Commit the status path and use the existing branch push. Finish the timer before the final status and report push.
3. Update `specs/bridge-watcher/requirements.md` with the success criteria and the independent check. Read both snapshots and the report directly from a bare Git origin.
4. Run the full suite and lint again. Check Git lock contention, slow or failed pushes, status output that contains credentials, and final output that arrives just before exit. The status path must preserve the agent's staged work. Keep routing, report naming, seen-marking, and hooks in their current scope.

The current branch is `bridge/watcher-run-status` at `b9ee980`. The watcher source is unchanged. The tests and report are present in the working tree. There is no new commit, push, or CI result.
