# Bridge watcher: Codex automation — requirements

## What it does

The watcher runs on Charles's Dell. It polls the Iris repo for new
Bridge prompt files and runs Muse Code headless on them, so Charles
does not have to paste prompts into PyCharm. This change adds Codex
as a second agent: the watcher also polls for Codex prompt files
and runs Codex headless on them, once each. Main still writes the
prompt and Charles still checks it. The watcher only removes the
paste, not the check.

## How it works

- A Codex prompt file is named `*-codex-prompt.md` in the same
  `prompts/` directory on a watched branch. A Muse Code prompt
  file keeps the name `*-prompt.md`.
- The watcher decides the agent from the filename before
  downloading. No file read is needed to route.
- For a Codex prompt, the watcher downloads the file (raw URL),
  then runs Codex headless with the prompt from the file, in
  `repo_dir`, with auto-approval so it does not stop and ask.
  The exact non-interactive invocation is confirmed by the
  implementer against the real Codex CLI on the Dell before any
  code is written (Step 0). The model default is GPT-6.2-Sol.
- It records the file as seen, so it never runs the same prompt
  twice, whatever the exit code. It logs every launch: time,
  branch, file, agent, and exit code.
- The Muse Code path is unchanged.

## Config file

No new config keys in v1. The existing keys (`branches`, `repo`,
`prompts_dir`, `poll_interval_seconds`, `state_file`, `log_file`,
`download_dir`, `repo_dir`) work for both agents. The Codex model
and flags live in code, not config.

## Report files

- A Muse prompt `foo-prompt.md` keeps its report at
  `foo-report.md` on the same branch, as now.
- A Codex prompt `foo-codex-prompt.md` keeps its report at
  `foo-codex-report.md` on the same branch.
- A prompt whose report already exists on the branch is marked
  seen and skipped, as now, for either agent.

## Log shape

Every launch is one line: time, branch, file, agent, and exit
code. Muse launches log `agent=muse`. Codex launches log
`agent=codex`. The README documents the new shape.

## Success criteria

- A `*-codex-prompt.md` file pushed to a watched branch is picked
  up within one poll interval and Codex runs it headless.
- A Codex prompt is never run twice, even across a restart.
- Every Codex launch is logged with time, branch, file,
  agent=codex, and exit code.
- The Muse Code path is unchanged: the existing test suite is
  green, no behavior change.
- The Codex command was verified live on the Dell against the
  real CLI, not only in tests.
- The README documents the Codex prompt naming and the new log
  shape.

## Non-goals

- It does not approve merges or open pull requests. It does not
  commit. It does not review. It only launches Codex.
- It does not edit the prompt. It runs the file as downloaded.
- It does not add a per-prompt model override, a usage cap, or a
  config knob for the Codex model in v1.
- It does not change the checkout/fetch logic. The
  fetch-before-checkout fix lives on its own branch.
- It does not load the watcher on the Dell. Charles loads it by
  hand (Task Scheduler) after this change merges.

## Watcher history: checks this change must satisfy

These are checks, not wishes. Each names a test or a verification
step that must exist. They come from the watcher bug history
(Bugs 1–6, the Nov 24 cascade, the pending fetch fix).

- A test that mocks the runner cannot catch a bug in the runner.
  The Codex runner is verified against the real Codex CLI on the
  Dell, not only against a fake runner in tests.
- Code that launches an external program is verified on the target
  platform against the real program (Windows, `codex` on PATH).
- A runner that launches an agent sets the working directory
  explicitly (`cwd=repo_dir`).
- A runner that launches an agent passes the flags the agent needs
  to run with the project's rules loaded (AGENTS.md).
- A runner uses the same checkout logic as the Muse path. It does
  not add a second checkout path.
- Output streams to the watcher terminal. Nothing is captured.
- A run is marked seen whatever its exit code, before logging.

## Decisions

- Routing by filename suffix (`*-codex-prompt.md`), not a marker
  inside the file, not a separate prompts dir. (Charles, Nov 26,
  2026, plan approval.)
- Single watcher, not a separate Codex watcher script. (Charles,
  Nov 26, 2026, plan approval.)
- Model default GPT-6.2-Sol, no per-prompt override in v1.
  (Charles, Nov 26, 2026, plan approval.)
- No new config keys in v1. (Charles, Nov 26, 2026, plan
  approval.)
- Branch `bridge/watcher-codex`, base main `b886e05`.
- Implementer: Codex (GPT-6.2-Sol, High), because Muse Code usage
  is out until Nov 29, 7:00 PM and Gemini quota is out until
  Dec 3.
