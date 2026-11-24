# Bridge watcher: check out the prompt's branch before running it

## What happened

The watcher polls a branch for new prompt files, downloads each
one, and runs Muse Code in the repo directory (`repo_dir`).
It runs Muse in the repo as-is: it never checks out the prompt's
branch first.

On Nov 24, 2026, the Muse Code Connect prompt ran while the
local repo was on a different branch than the prompt's branch
(`app-mode/connect-muse-code`). The spec
(`specs/app-mode-connect-muse-code/requirements.md`) and the 16
failing tests (`tests/test_appmode_connect_muse_code.py`) live on
the prompt's branch, so they were not in the working tree. Muse
Code implemented the feature from the prompt text alone, without
the spec and without running the 16 tests, and it looked for the
tests on the wrong branch name (`app-mode/connect`) before
concluding they did not exist. They did exist, on the prompt's
branch, verified by the reviewer.

This is watcher Bug 6. The runner's working tree must match the
prompt's branch, or the prompt's own files are missing.

## The fix

In `run_once` in `tools/bridge_watcher.py`, before running a
prompt from branch B, ensure the working tree in `repo_dir` is
on branch B. Switch to it with a local checkout when it is not
already there.

Safeguards, all in plain words in the log when they trigger:

- Never lose uncommitted work. If the working tree has
  uncommitted changes that a checkout would lose, skip the run,
  do NOT mark the prompt seen, and log why. A later poll retries
  it once the tree is clean.
- If branch B does not exist locally, skip the run, do NOT mark
  the prompt seen, and log why. Do not fetch: the watcher has no
  credentials for that. The branch must be present locally.
- If the checkout itself fails, skip the run, do NOT mark the
  prompt seen, and log why.
- A prompt that is skipped this way is not a failure of the
  prompt. It stays unmarked so a later poll retries it.

The baseline rule is unchanged: baselining marks files seen and
runs nothing, and needs no checkout.

## Tests first

Write the failing tests first, following the existing patterns
in `tests/test_bridge_watcher.py`. Cover: checkout happens
before the run when the tree is on another branch; no checkout
when already on the branch; a dirty tree skips the run and
leaves the prompt unmarked; a missing local branch skips the
run and leaves the prompt unmarked; a failed checkout skips the
run and leaves the prompt unmarked. Run the suite and show the
new tests failing before the fix and passing after it.

## Rules

- Follow the repo rules in `AGENTS.md`: pytest only, tests
  committed separately from the fix, never edit tests during
  implementation. Report the commit hash, the push result, and
  the test results, then stop.
- Do not hardcode any branch or path. The branch comes from the
  prompt's branch; the repo dir comes from the config.
- Push the branch only. Do not open a PR. Do not merge.
- This run is hand-triggered in PyCharm, not launched by the
  watcher. The watcher cannot fix itself through itself.
