# Bridge watcher: run-status reporting so progress is visible from GitHub (Codex)

## Task

Add run-status reporting to the Bridge watcher (`tools/bridge_watcher.py`) so a run's progress can be read from GitHub while it is happening, without watching the watcher terminal.

The problem: the watcher streams an agent's output to the terminal on the Dell only. Main (MuseHog) cannot see live progress. It only sees a run after it finishes, when the watcher pushes the report to GitHub. Charles has to watch the terminal himself to know where a run is.

The fix: while a run is in progress, the watcher writes a status file and pushes it to GitHub on a timer, so the run's progress is readable from the repo at any time.

## Challenge the frame

Before you start, if a simpler or sturdier way to make run progress visible from GitHub exists, say so once up front, then do what this prompt asks.

## What to build

1. **Status file.** For a prompt `prompts/<name>-prompt.md` (or `prompts/<name>-codex-prompt.md`), the watcher writes `prompts/<name>-status.md` while the run is in progress. The file contains:
   - Agent (`muse` or `codex`)
   - Prompt name
   - Started time (UTC, ISO format)
   - Last updated time (UTC, ISO format)
   - State: `running`, `done`, or `failed`
   - The last ~50 lines of the agent's output, so a reader can see where the run is
   Keep the file small. Do not include the full output.

2. **Push cadence.** Push the status file to GitHub every 2 minutes during a run. Do not push on every line of output (too many commits). A timer-based push is the rule; a line-count trigger is acceptable only as an additional trigger, not a replacement for the timer.

3. **On finish.** When the run ends, update the status file to `done` (or `failed` if the run failed) with the final timestamp, and push it together with the report (the existing push-after-run behavior). Leave the status file in place afterward. The next run for the same prompt overwrites it.

4. **All agents, shared path.** The status reporting must work for EVERY agent the watcher routes to — Muse and Codex today. Put the status writing in the shared run path that both `run_muse` and `run_codex` go through, or in a shared helper both call. Do not duplicate the status logic per agent. Design it so that if a new agent is added to the watcher later, it gets status reporting the same way, with no extra work.

## What must not change

- The watcher still streams the agent's output to the terminal (nothing captured away from the terminal).
- The report contract is unchanged: the agent writes `prompts/<name>-report.md`, the watcher pushes the branch after the run.
- Seen-marking is unchanged: a run is marked seen whatever its exit code.
- Skip-if-report-exists is unchanged: a prompt whose report already exists on the branch is skipped as already-run.
- Agent routing is unchanged: routing is by filename suffix only (`codex` if the name ends in `-codex-prompt.md`, else `muse`).
- The watcher stays stdlib-only Python. No new dependencies.

## Steps

1. Write failing tests first that prove the behavior: a status file is written during a run; it contains the agent, prompt, started time, and recent output; it updates on the timer; it marks `done`/`failed` when the run ends; it works for both the Muse path and the Codex path. Add them to the watcher tests (`tests/test_bridge_watcher.py` or the equivalent watcher test file).
2. Implement the status reporting.
3. Run the full pytest suite. All existing watcher tests plus your new tests must pass, and the full suite stays green.
4. Run the lint gate (ruff + pyright) clean.
5. Write the report.

## Constraints

- Stdlib-only Python, no new dependencies.
- Do not change the agent routing, the report contract, the seen-marking, or the skip-if-report-exists rule.
- Do not push on every line of output. The 2-minute timer is the push rule.
- Keep the status file small (last ~50 lines, not the full output).
- The hooks must keep working. This change touches the watcher only, not the hooks.
- Do not invent behavior not listed here. Do not redesign the watcher.

## Report

Write the report to `prompts/bridge-watcher-run-status-codex-report.md`. Include: what was built (one line per item above, with the test that proves it), the full test results (count passed/failed), the lint results, and anything not done with why. Note explicitly that the status reporting works for both the Muse and Codex paths.
