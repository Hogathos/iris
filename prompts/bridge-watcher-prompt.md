# Bridge watcher (the runner) — full-context Bridge prompt

Implement the Bridge watcher from the spec. Read the spec first,
then the rules, then the code.

## Read

- `specs/bridge-watcher/requirements.md` — the spec. It is the
  success criteria.
- `AGENTS.md` — the working rules.
- `docs/bridge.md` — the Bridge itself. The watcher is the
  automation half of it. It launches Muse Code, it does not replace
  the check.

## Do

1. Write `tools/bridge_watcher.py`: a small Python script (standard
   library only, no new dependencies). It polls the GitHub API for
   the `prompts/` directory on each branch in the config, compares
   against a seen-list on disk, downloads each new prompt file, and
   runs `muse exec --prompt-file <path> --disable-approval`.
   It logs every launch (time, branch, file, exit code). It never
   runs the same prompt twice, even across a restart. A network
   blip does not kill it; it retries on the next poll.
2. Write `tools/bridge_watcher_config.json`: the config file. Fields
   per the spec. Default branches `["app-mode/packaging"]`.
   Default poll interval 120 seconds.
3. Write `tools/bridge_watcher_README.md`: how Charles installs it
   on the Dell. Python 3, no extra packages. Task Scheduler at
   login. How to edit the config to switch branches or watch
   several. How to read the log. How to stop it.
4. Write tests for the pure logic: the seen-list (never re-runs),
   the branch filter (does not run unwatched branches), the config
   parsing (default values, bad values rejected with a plain-words
   error). Put them in `tests/test_bridge_watcher.py`. Use the
   failing-test-first flow: write the tests, show them, then
   implement. Do not edit the tests during implementation.
5. Run the full suite (`python -m pytest tests/ -q`) plus
   `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`, and pyright.
   All must pass. A watcher change must not break the suite.

## Rules

- Standard library only. No new dependencies. The Dell runs plain
  Python 3.
- The watcher never merges, never opens a pull request, never
  commits, never reviews. It only launches Muse Code.
- It never runs a prompt twice. The seen-list is the guarantee.
- It never runs prompts on a branch not in the config. It never
  runs prompts in `main` unless Charles puts `main` in the config
  himself.
- Commit named files only. Never `git add -A`, never
  `git commit -a`. Never commit `.exe`, `dist/`, `build/`,
  `*.db`, `.pytest_cache/`, or secrets. The seen-list and the log
  live on the Dell, not in the repo. Do not commit them.
- Push the branch only. Do NOT open a PR or merge.
- Report the commit hash, the test results, and what is still open
  (the Task Scheduler install is Charles's step, by hand). Then
  stop. Do not report session usage; the usage panel is not
  readable from inside the session, Charles screenshots it.
