# Bridge prompt: Watcher Codex automation — Step 2, implementation

## What this prompt authorizes

Step 2 ONLY: implement the Codex support in the watcher, make the
failing tests pass, commit locally, and STOP for review. Do NOT
push. Do NOT merge. Do NOT open a pull request. After the
implementation is committed, write your report and stop. Main
reviews the implementation, Charles approves it in words, and a
merge decision follows.

## Challenge the frame

Before you start, if a simpler or sturdier way exists to do what
this prompt asks, say so once, up front, in your report: "Are you
sure you want X? Y is simpler because Z." Then do what the prompt
says. Challenge the frame, not just the details. Silence means
you checked and this is the best path. Say it once, do not nag it
again.

## Base and setup

1. Fetch origin FIRST: `git fetch origin`.
2. Switch to branch `bridge/watcher-codex`: run
   `git checkout bridge/watcher-codex`. If the checkout fails
   because the working tree has uncommitted changes, say so in
   your report and stop: do NOT discard changes, do NOT stash
   without being told to. If the checkout fails because the
   branch does not exist, say so in your report and stop. After
   checkout, verify with `git branch --show-current` that you
   are on `bridge/watcher-codex`. If you are not, say so in your
   report and stop.
3. Verify the base: `git log --oneline -3` must show `eb7f5c7`
   (the Step 1 tests commit) on top of `ca9283f` (the prompt
   fix). If the base is different, say so in your report and
   stop.
4. Activate the venv first: the repo venv is `.venv314`. Run
   `C:\Users\Quant\Documents\iris\.venv314\Scripts\Activate.ps1`
   (or the equivalent for your shell) before running Python or
   pytest. Do NOT run tests from a venv that lacks the packages.
5. Read the spec first: `specs/bridge-watcher-codex/requirements.md`.
   Read the tests Step 1 wrote: `tests/test_bridge_watcher.py`,
   the `CodexPromptNamesTest`, `CodexCommandTest`, and
   `CodexRunOnceTest` classes. Read the current watcher:
   `tools/bridge_watcher.py`. Read the Step 1 report:
   `prompts/bridge-watcher-codex-tests-report.md` (local, may
   not be on the branch; the test names and the Step 0 findings
   are summarized below).

## What Step 1 verified

Step 0 confirmed the non-interactive Codex invocation on the Dell
(Windows). The verified command is a PowerShell wrapper that reads
the prompt file as UTF-8 and pipes it to Codex on stdin:

```
powershell.exe -NoProfile -NonInteractive -Command "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); Get-Content -LiteralPath '<prompt path>' -Raw -Encoding UTF8 -ErrorAction Stop | & codex -a never exec --sandbox workspace-write --ephemeral -m gpt-6.2-sol -; exit $LASTEXITCODE"
```

Step 1 wrote nine tests. Two pass already (the filename rules
already cover both agents). Seven fail because the Codex functions
do not exist yet. Your job is to make those seven pass, without
breaking anything else.

## Step 2: implement

Implement the Codex support in `tools/bridge_watcher.py`,
following the existing watcher style (stdlib only, plain-words
docstrings, functions the tests can import).

1. **`build_codex_command(path)`** — Return the exact command
   list the Step 1 test asserts: the PowerShell wrapper above,
   with the prompt path in it. The path may contain spaces, an
   apostrophe, and an ampersand; use `-LiteralPath` and the
   PowerShell single-quote doubling (`''` for `'`) exactly as
   the test expects. The model is `gpt-6.2-sol`. The flags are
   `-a never`, `exec`, `--sandbox workspace-write`,
   `--ephemeral`, `-m gpt-6.2-sol`, `-` (read prompt from
   stdin). Do NOT invent a different invocation. The test pins
   the shape; the live behavior was verified in Step 0.

2. **`run_codex(command, cwd)`** — Run the command as a
   subprocess with `cwd` set to the given directory (the repo
   dir). Do NOT capture stdout or stderr; output streams to the
   terminal, exactly like the Muse runner does now. Return the
   exit code. This is the history check: a runner that launches
   an agent sets the working directory explicitly, and output
   streams to the watcher terminal, nothing is captured.

3. **`run_once` routing** — Accept an injected `codex_runner`
   callable (like the existing `runner` parameter for Muse).
   Route by filename before downloading: a `*-codex-prompt.md`
   file goes to the Codex builder and the Codex runner; a
   `*-prompt.md` file goes to the Muse builder and the Muse
   runner. A Codex prompt must never call the Muse runner, and
   vice versa. Use the same checkout logic as the Muse path.
   Do NOT add a second checkout path. This is a history check.

4. **Log shape** — Every launch logs one line with time,
   branch, file, agent, and exit code. A Codex launch logs
   `agent=codex`; a Muse launch logs `agent=muse`. The existing
   `log_launch` must record the agent. Do NOT change the log
   format for Muse launches beyond adding the agent field.

5. **Seen, whatever the exit code** — A run is marked seen
   whatever its exit code, before logging. A failed Codex run
   (non-zero exit) is marked seen and never re-runs, exactly
   like a failed Muse run. A prompt already in the seen list is
   skipped, even across a restart. This is a history check (Bugs
   1 and 2: a run is marked seen whatever its exit code).

6. **README** — Document the Codex prompt naming
   (`*-codex-prompt.md`) and the new log shape
   (`agent=codex` / `agent=muse`) in the watcher README, as
   the spec requires. Keep the Muse documentation unchanged
   except where the log shape is described.

7. **Muse path unchanged** — Do NOT change Muse behavior. The
   existing Muse tests must all still pass. If you must touch a
   shared function (for example, `run_once` or `log_launch`),
   keep the Muse call path identical.

History checks this change must satisfy (from the spec; each
names a test or a verification step that must exist):

- The Codex runner is verified against the real Codex CLI on
  the Dell, not only against a fake runner in tests. (Step 0
  did this; your implementation must use the verified command,
  not a different one.)
- Code that launches an external program is verified on the
  target platform against the real program (Windows, `codex`
  on PATH). (Step 0 did this; do not change the command
  without re-verifying it live.)
- A runner that launches an agent sets the working directory
  explicitly (`cwd=repo_dir`).
- A runner that launches an agent passes the flags the agent
  needs to run with the project's rules loaded (AGENTS.md).
  (The verified command reads the prompt from stdin and Codex
  loads AGENTS.md by default, per Step 0. Do not add a flag
  that changes this.)
- A runner uses the same checkout logic as the Muse path. It
  does not add a second checkout path.
- Output streams to the watcher terminal. Nothing is captured.
- A run is marked seen whatever its exit code, before logging.

Rules for the implementation:

- Do NOT modify the tests. The tests are the spec. If a test
  is wrong, say so in your report and stop: do NOT change the
  test to fit your implementation.
- Do NOT add a config knob for the Codex model, a usage cap,
  or a per-prompt model override in v1. The model lives in
  code, not config, per the spec.
- Do NOT change the checkout/fetch logic. The
  fetch-before-checkout fix lives on its own branch.
- Do NOT load the watcher on the Dell. Charles loads it by
  hand (Task Scheduler) after this change merges.
- Run the tests as you go. The seven failing tests must pass
  when you are done. The two passing tests must still pass.
  The full existing suite must still pass.

## Verification

Before committing, run all of these and report the results:

1. The nine Codex tests: `python -m pytest
   tests/test_bridge_watcher.py -q -k Codex` — all nine must
   pass.
2. The full suite: `python -m pytest tests/ -q` — must be
   green (no failures beyond the seven you just fixed).
3. Ruff check: `ruff check tools/bridge_watcher.py` — must
   pass with no errors.
4. Ruff format check: `ruff format --check
   tools/bridge_watcher.py` — must pass. (If the file already
   failed formatting before your change, say so in your report
   and do NOT reformat unrelated lines. Format only your new
   code if the checker requires it.)
5. Pyright: `pyright tools/bridge_watcher.py` — must pass
   with no errors.

If any gate fails, fix your implementation, not the test. If a
gate fails because of a pre-existing condition in a file you did
not touch, say so in your report and do NOT reformat or change
that file.

## Commit and report

1. Stage named files only, for example: `git add
   tools/bridge_watcher.py` and `git add` the README file you
   changed, each by name. Never `git add -A`. Never `git
   commit -a`.
2. Commit locally with a plain-words message, for example:
   `Watcher Codex: implement Codex routing and command`.
   Do NOT push. No push in Step 2.
3. Write your report to
   `prompts/bridge-watcher-codex-implementation-report.md` on
   this branch. The report must contain:
   - "Implementation" — what you changed, file by file, and
     the commit hash.
   - "Tests" — the nine Codex tests now pass; the full suite
     result; Ruff and Pyright results.
   - "History checks" — each of the seven history checks,
     and how your implementation satisfies it.
   - "Challenge the frame" — your one-line check, or silence
     if this is the best path.
   - "What is still open" — anything you could not verify
     (CI, a live watcher run), and the commit hash.
4. STOP. Do not push. Do not merge. Main reviews the
   implementation and Charles approves it in words before any
   merge.

## Files you may touch in this step

- `tools/bridge_watcher.py` (implement the Codex support)
- The watcher README (document the Codex naming and log shape)
- `prompts/bridge-watcher-codex-implementation-report.md` (new report)

## Files you must NOT touch in this step

- `tests/test_bridge_watcher.py`
- Any other file.
