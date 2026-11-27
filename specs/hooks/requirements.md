# Hooks as deterministic gates — requirements

## What it does
Moves the repo's load-bearing rules out of prose (prompts, AGENTS.md) and
into hooks that run whether the agent agrees or not. Four hooks:
1. Banned files (pre-commit). Blocks a commit if any staged file matches
   the repo ban list (AGENTS.md: `.exe`, `dist/`, `build/`, `*.db`,
   `demo_*.db`, `.pytest_cache/`, `*.spec`, secrets by filename pattern).
   One exception: the already-tracked `iris.spec` at repo root.
2. Fast lint + types (pre-commit). Runs `ruff check`, `ruff format --check`,
   and `pyright` on the staged Python files and uses the staged file contents.
   Ruff honors the staged exclude configuration. A failed check blocks the commit.
3. Unplanned files (pre-commit). Compares staged files against the
   approved plan's file list. The approved plan is a file named
   `approved-plan.txt` at the repo root, one approved file path per line,
   relative to repo root. Blocks the commit if a staged file is not
   in the plan. Deletions count as staged changes. A human can stage an
   updated plan with newly approved files. A staged plan deletion blocks the commit.
4. Muse Code PreToolUse hook. Runs before every tool call in Muse Code
   sessions. A script (`.muse/hooks/iris-pre-tool-use.py`, stdlib-only
   Python) wired to the PreToolUse event in `.muse/hooks.json` reads the
   tool-call payload on stdin and denies (exit 2) any call that violates
   the rules: shell commands matching the ban patterns (`rm -rf`,
   `git add -A`, `git commit -a`, `git push --force`, `git reset --hard`);
   writes or edits to banned files (same ban list as hook 1); writes or
   edits to files outside the approved plan. File mutations require a readable
   plan at the repository root. Muse denies changes to the plan itself.
   The hook runs locally without a network call or new dependency.

The installer uses Git's active hooks directory. It saves a copy of a custom
pre-commit hook before replacing it.

## Acceptance criteria
- A commit staging a banned file is blocked, and the block names the file.
- A commit staging only clean files passes.
- A commit with a lint, format, or type failure is blocked, and the block
  names the failure.
- A commit staging a file not in the approved plan is blocked, unless the
  plan file itself is staged.
- A Muse Code tool call matching a ban pattern is denied before it runs.
- A Muse Code write to a banned file is denied before it runs.
- A Muse Code write to a file not in the approved plan is denied before
  it runs.
- A missing or unreadable plan causes Muse to deny file mutations.
- A shell denial names the rule and omits the submitted command.
- A clean Muse Code tool call passes the hook.
- The full pytest suite passes, and CI is green.
- The hooks are verified on Windows (the Dell), where commits happen.

The installer check uses a temporary Git repository with a configured hooks
path. Git itself invokes the installed hook and blocks a commit with a banned
file.

## Non-goals
- The `pre-commit` framework itself (steal #8, separate).
- Mutation testing + Hypothesis properties (steal #3, separate).
- Agent-level hooks for Anti-Gravity (no evidence found of an equivalent
  system; unverified).
- Changing the lint gate or dropping flake8 (the queued tooling change owns
  that; this change only runs the existing gate earlier).
- A watcher-level post-run check (the Stop-hook equivalent is already CI +
  Main's verification gate).
- A Muse Code Stop hook (Stop is an official event, but this change keeps
  CI + Main's verification gate as the Stop layer).

## Decisions
- Plain git hooks, not the `pre-commit` framework. No new dependency;
  stdlib-only Python, matching the watcher style. (Nov 25)
- The hook runs `ruff check` + `ruff format --check` + `pyright`, not
  flake8. Flake8 is being dropped by the queued tooling change, and ruff
  covers the same hard-error gate (E9/F63/F7/F82). (Nov 25)
- Unplanned-files hook: block, not warn-only. A warning is prose the agent
  can ignore; a block is a check that can fail. The escape hatch is staging
  the plan file. (Charles, Nov 25)
- `ruff format --check` stays in the fast hook. It makes the hook a local
  preview of CI (the queued tooling change adds the same check to CI), so a
  commit that passes the hook will not fail CI on formatting. The fix for a
  failure is one command (`fmt`). (Charles, Nov 25)
- Muse Code PreToolUse hook (hook 4) is folded into this plan, not a
  separate step. (Charles, Nov 25)
- The Muse Code hook is a plain local command, not a service. No Arcjet, no
  network call, no new dependency. (Nov 25)
- The approved plan is a file named `approved-plan.txt` at the repo root,
  one approved file path per line, relative to repo root. The tests pin
  this filename; the spec names it here so the two agree. (Charles, Nov 25
  — approved the mechanism in Main after the test review.)
