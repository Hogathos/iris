# Bridge prompt: Watcher Codex automation — Step 1, tests only

## What this prompt authorizes

Step 1 ONLY: write failing tests for the Codex automation, commit
them locally, and STOP for test review. Do NOT implement anything
yet. Do NOT push. After the tests are written and committed, write
your report and stop. Main reviews the tests, Charles approves
them in words, and a second prompt authorizes Step 2.

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
3. Verify the base: `git log --oneline -3` must show `e0da73b`
   (the spec + prompt commit) on top of `b886e05` (main, PR #63
   merge). If the base is different, say so in your report and
   stop.
3. Activate the venv first: the repo venv is `.venv314`. Run
   `C:\Users\Quant\Documents\iris\.venv314\Scripts\Activate.ps1`
   (or the equivalent for your shell) before running Python or
   pytest. Do NOT run tests from a venv that lacks the packages.
4. Read the spec first: `specs/bridge-watcher-codex/requirements.md`.
   Read the current watcher: `tools/bridge_watcher.py`. Read the
   existing tests: `tests/test_bridge_watcher.py` (2,052 lines).
   Read the plan: this prompt is Step 1 of
   `~/workspace/iris-watcher-codex/plans/watcher-codex-plan.md`
   (not in the repo; the plan is summarized in the spec and below).

## Step 0: verify the Codex invocation FIRST

Before writing any test, confirm the exact non-interactive Codex
command on this machine (the Dell, Windows):

1. Run `codex --help` and read the non-interactive options. Find
   the command that runs a prompt without interaction and exits
   (expected shape: `codex exec` with an auto-approval flag).
2. Run a live test: pass a trivial prompt (for example, `codex
   exec --full-auto "reply with the word OK and nothing else"`)
   and record the exit code and the visible output.
3. Confirm how a prompt FILE is supplied: does `codex exec`
   accept a file path, or must the file contents be passed as the
   argument? Record what you found.
4. Confirm `codex` resolves on PATH on this machine, and confirm
   the exit code reflects the run outcome (a failing run exits
   non-zero).
5. Confirm Codex reads AGENTS.md and the repo rules by default,
   or find the flag that makes it do so. Record the evidence.

Write the findings in your report under "Step 0: Codex invocation".
If `codex exec` cannot read a prompt from a file, say so plainly
and stop: the plan changes, and Step 1 does not proceed on a
guessed invocation. Do NOT write tests against a command you have
not verified.

## Step 1: write failing tests

Write tests in `tests/test_bridge_watcher.py`, following the
existing test style (unittest, `tempfile.TemporaryDirectory`,
plain-words docstrings). The tests must FAIL now, for the right
reason: the Codex functions do not exist yet.

Cover:

1. **Routing.** `is_prompt_file` returns true for
   `foo-codex-prompt.md` and for `foo-prompt.md`, and false for
   anything else (a report, a note, a bare `.md` file).
2. **Report naming.** `report_name("foo-codex-prompt.md")` is
   `foo-codex-report.md`. `report_name("foo-prompt.md")` is
   `foo-report.md`, as now.
3. **Codex command builder.** A new `build_codex_command` returns
   the exact command list you verified in Step 0, with the prompt
   path in it. The test asserts the command list shape and that
   the prompt path is present. (The live behavior is verified in
   Step 0 and again in Step 2; the test pins the shape.)
4. **Agent selection in `run_once`.** A Codex prompt routes to
   the Codex builder and the Codex runner; a Muse prompt routes
   to the Muse builder and the Muse runner. Use fake runners
   (injected callables, as `run_once` already accepts) and assert
   which runner was called with which command. A Codex prompt
   must never call the Muse runner, and vice versa.
5. **Log shape.** A Codex launch logs `agent=codex`; a Muse
   launch logs `agent=muse`. Assert the agent string appears in
   the log line alongside time, branch, file, and exit code.
6. **No-rerun for Codex.** A Codex prompt marked seen is never
   run again, even across a simulated restart (reload the
   seen-list from disk). A failed Codex run (non-zero exit) is
   marked seen and never re-runs.
7. **Working directory.** The Codex runner is invoked with
   `cwd=repo_dir`. Assert the runner receives the repo dir.
   (Use the injected runner to capture the call; the real
   `run_codex` must pass `cwd=repo_dir`, exactly like `run_muse`.)
8. **No output capture.** The Codex runner does not capture
   stdout or stderr. Assert the subprocess call does not pass
   `capture_output=True` (or equivalent). Output streams to the
   terminal, as Muse output does now.
9. **Muse path unchanged.** The existing Muse tests all still
   pass. Add no test that changes Muse behavior. If an existing
   test fails because of your test additions, say so in your
   report and fix the test addition, not the existing test.

Rules for the tests:

- The test writer is not the implementer. You are the test
  writer now. Write the tests; a second prompt implements.
- Do NOT modify `tools/bridge_watcher.py` in this step. Tests
  only. If you touch the module, you have gone past Step 1.
- Do NOT modify any existing test. Add new test classes or
  methods only.
- Run the new tests and confirm they FAIL for the right reason
  (the Codex functions do not exist: `AttributeError` or
  `ImportError`, not a logic error in your test). Run the full
  existing suite and confirm it is still green. Report both
  results.

## Commit and report

1. Stage named files only: `git add tests/test_bridge_watcher.py`.
   Never `git add -A`. Never `git commit -a`.
2. Commit locally with a plain-words message, for example:
   `Watcher Codex: failing tests for Codex routing and command`.
   Do NOT push. No push in Step 1.
3. Write your report to
   `prompts/bridge-watcher-codex-tests-report.md` on this branch.
   The report must contain:
   - "Step 0: Codex invocation" — the exact command, the flags,
     how the prompt file is supplied, the live test exit code and
     output, the AGENTS.md evidence, and anything you could not
     verify.
   - "Tests written" — the test names, the count, and the failure
     output (each new test fails for the right reason).
   - "Existing suite" — the full suite result (must be green).
   - "Challenge the frame" — your one-line check, or silence if
     this is the best path.
   - "What is still open" — anything you could not verify, and
     the commit hash.
4. STOP. Do not implement. Do not push. Main reviews the tests
   and Charles approves them in words before Step 2.

## Files you may touch in this step

- `tests/test_bridge_watcher.py` (add tests only)
- `prompts/bridge-watcher-codex-tests-report.md` (new report)

## Files you must NOT touch in this step

- `tools/bridge_watcher.py`
- Any other file.
