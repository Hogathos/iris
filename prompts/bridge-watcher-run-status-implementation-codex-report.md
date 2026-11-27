# Bridge watcher run-status implementation

## Approach

Keep the status files on the run branch. A separate status branch would avoid
live commits on the run branch, but readers would need a second branch and the
bare-origin checks require the snapshots on the run branch. Use a
path-only Git commit and a timed publisher. A slow push can then delay
publication without holding up terminal output.

## Files changed

- `tools/bridge_watcher.py` adds run-status reporting for both the Muse and
  Codex paths. It writes a local snapshot before launch. A timer publishes
  updates every two minutes, including when the agent is silent. The shared
  reader forwards stdout and stderr together to the terminal as the bytes
  arrive. It keeps up to 50 lines for the status file and omits detected
  credentials. The watcher drains output before the final snapshot and
  publishes it in the same push as the agent's committed report.
- `specs/bridge-watcher/requirements.md` records the status success criteria
  and the independent check against a bare origin.
- This report records the verification and handoff.

The Codex status basename drops the `-codex` suffix, as the approved
test expects. The report basename keeps it. The installed Git hook
checks whole-file formatting, so Ruff formatted the watcher file.

## Verification

| Check | Result |
| --- | --- |
| Full suite before implementation, from Step 1 | 434 passed, 3 expected failures, and 46 subtests passed. |
| Full suite before implementation, this session | 434 passed and 46 subtests passed. The timer test failed as expected. The two origin tests hit a pytest temporary folder permission error. |
| Approved tests against the original watcher with a writable temporary folder | All 3 failed as expected. The timer constant and initial snapshots were absent. |
| Full suite after implementation | 437 passed and 46 subtests passed in 238.23 seconds. This includes all three approved tests. |
| Ruff and Pyright | Ruff lint and the watcher's format check passed. Pyright reported 0 errors and 0 warnings. |
| Credential signature scan | 0 matches in the changed text. |

- The final suite used `python -m pytest tests/ -q` with the cache provider
  disabled and a writable base directory for pytest's temporary files.
- The bare-origin read-back covered two live snapshots during silence, the
  final snapshot, and the report for each route. Muse finished as `done`
  with exit 0. Codex finished as `failed` with exit 7. Both retained the line
  written just before exit and preserved an unrelated staged file. A
  separate check also retained a final line without a newline.
- A temporary repo with the Iris hooks installed accepted a status-only
  commit. A held index lock blocked a snapshot commit. Releasing the lock
  let a later commit succeed and preserved the staged file's exact blob.
- A rejecting pre-push hook and a slow pre-push hook exercised push failure
  and timeout. A subsequent push reached the bare origin. The remote
  snapshot omitted synthetic credentials from a named environment
  variable, a header, a password field, and a private-key block.
- Both demo scripts ran on a temporary seeded database. They reported 6
  attempts, 3 traces, 2 successes, and 3 retries. The visualizer reported
  $0.0120 in recorded demo cost.

## Open items

- Credential filtering covers recognized formats and values from known
  credential environment variables. Unknown formats can pass through.
- If the final commit or push fails, the origin can show an older snapshot
  until the branch is published. On Windows, a Git hook subprocess can
  outlive a timed-out Git push.
- A live branch push can also publish commits the agent already made on
  that branch. Other staged files stay in the local index.
- Muse and Codex prompts with the same status basename on one branch
  share a status path. The later run writes the next snapshot at that path.
- Charles has the commit and GitHub push. Verification here used temporary
  local origins. The watcher and spec changes are in the working tree.
  CI remains to be checked for Charles's eventual commit.
