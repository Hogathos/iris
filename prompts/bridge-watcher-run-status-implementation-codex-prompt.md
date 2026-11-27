# Bridge watcher: implement run-status reporting (Codex) — Step 2, tests approved

## Context

Step 1 is done and Charles reviewed and approved the tests. Three failing tests are in
`tests/test_bridge_watcher.py` (lines 2503-2666) on branch `bridge/watcher-run-status`:
`test_run_status_uses_a_two_minute_timer`, `test_run_status_muse_publishes_live_output_and_finishes_done`,
and `test_run_status_codex_publishes_live_output_and_finishes_failed`. They fail because the
feature is not implemented yet. Implement it so all three pass.

## Challenge the frame

Before implementing, if a simpler or sturdier way exists than the approach in the report,
say so once, up front, in the report. Then do the chosen approach. Challenge the frame,
not just the details.

## What to build

Add the shared status flow to `tools/bridge_watcher.py` so a run's progress is visible
from GitHub while it is happening. The tests define the behavior; in short:

- Write the first status snapshot at launch, before the agent starts, so the agent finds
  the status file present. The status file is `prompts/<name>-status.md` and contains the
  agent, prompt name, UTC Started and Last updated timestamps, State (`running`, `done`,
  or `failed`), and the last ~50 lines of output.
- Forward the agent's output to the terminal as it arrives (streaming unchanged) and keep
  a bounded tail for the status file.
- Push the status file every 2 minutes during a run, even when the agent is silent. Use a
  timer (`STATUS_INTERVAL_SECONDS` = 120). Do not push on every line of output.
- On finish, update the status to `done` (exit 0) or `failed` (non-zero exit) with the final
  timestamp, and push it together with the report. Leave the status file in place afterward.
- Put the status writing in the shared run path both `run_muse` and `run_codex` go through,
  or a shared helper both call. Do not duplicate it per agent.
- The status push must preserve the agent's staged work: it commits only the status file,
  never anything else the agent staged.

## What must not change

- Terminal streaming is unchanged.
- The report contract is unchanged: the agent writes `prompts/<name>-report.md`, the watcher
  pushes the branch after the run.
- Seen-marking is unchanged. Skip-if-report-exists is unchanged. Agent routing is unchanged
  (filename suffix only). The watcher stays stdlib-only Python. No new dependencies.
- The hooks must keep working. This change touches the watcher only, not the hooks.

## Steps

1. Implement the status reporting in `tools/bridge_watcher.py` so the three approved tests pass.
2. Update `specs/bridge-watcher/requirements.md` with the success criteria and the independent
   check (read both snapshots and the report directly from a bare Git origin).
3. Run the full pytest suite. All existing watcher tests plus the three new tests must pass,
   and the full suite stays green.
4. Run the lint gate (ruff + pyright) clean.
5. Write the report. Do not stop for approval at any point in this run — this session is
   non-interactive, so work through to the report.

## Checks to run while implementing

- Git lock contention, slow or failed pushes, status output that contains credentials, and
  final output that arrives just before exit. The status path must preserve the agent's
  staged work. Keep routing, report naming, seen-marking, and hooks in their current scope.

## Report

Write the report to `prompts/bridge-watcher-run-status-implementation-codex-report.md`.
Include: what changed (files), the approach, full test results (before/after), lint results,
and anything still open. Before finishing, confirm the report file exists on disk and the
code changes are present in the working tree. Note explicitly that the status reporting
works for both the Muse and Codex paths.

Note: you cannot commit or push (Git is read-only in this workspace); Charles does the git
part. Do not claim a push or CI result you did not produce.
