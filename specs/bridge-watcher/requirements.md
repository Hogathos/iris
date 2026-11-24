# Bridge watcher (the runner) — requirements

## What it does

The watcher runs on Charles's Dell. It polls the Iris repo for new
Bridge prompt files and runs Muse Code headless on them, so Charles
does not have to paste prompts into PyCharm. It is the automation
half of the Bridge. Main still writes the prompt and Charles still
checks it. The watcher only removes the paste, not the check.

## How it works

- The watcher is a small Python script. It runs at login on the
  Dell (Task Scheduler). It polls on a timer.
- It watches a list of branches, from a config file. Default: the
  current app-mode branch. The list is configurable, so Charles can
  switch branches or watch several at once without changing code.
- For each watched branch, it lists the `prompts/` directory in the
  repo (GitHub API). It compares against a seen-list it keeps on
  disk.
- For each prompt file it has not seen, it downloads the file
  (raw URL), then runs:
  `muse exec --prompt-file <path> --disable-approval`
  `--disable-approval` turns off approval prompts but keeps the
  sandbox. Muse Code reads the prompt, implements, pushes the
  branch, and reports, per the prompt's own instructions.
- It records the file as seen, so it never runs the same prompt
  twice. It logs every launch: time, branch, file, exit code.

## Config file

A JSON file next to the script. Fields:
- `branches`: list of branch names to watch. Default
  `["app-mode/packaging"]`. Change it to switch branches. Add
  entries to watch several at once.
- `repo`: `"Hogathos/iris"`. Do not hard-code it elsewhere.
- `prompts_dir`: `"prompts"`. The directory in the repo to watch.
- `poll_interval_seconds`: how often to check. Default 120.
- `state_file`: path to the seen-list. Default a file next to the
  script. It survives restarts, so a prompt is never re-run after
  a reboot.
- `log_file`: path to the launch log. Default a file next to the
  script.
- `download_dir`: where downloaded prompt files go. Default a
  folder next to the script.

## Success criteria

- A new prompt file pushed to a watched branch is picked up within
  one poll interval and Muse Code runs it headless.
- A prompt is never run twice, even across a restart.
- Every launch is logged with time, branch, file, and exit code.
- A network blip does not kill the watcher. It retries on the next
  poll and keeps going.
- It does not run prompts on branches that are not in the config.
- It does not run prompts in `main`. Main is not a watched branch
  unless Charles puts it in the config himself.

## Non-goals

- It does not approve merges or open pull requests. It does not
  commit. It does not review. It only launches Muse Code.
- It does not edit the prompt. It runs the file as downloaded.
- It does not install Muse Code or the Task Scheduler entry. Charles
  installs those once, by hand, from the README the implementer
  writes.
- It does not watch the repo for code changes. Only prompt files
  in `prompts/`.

## Decisions

- Watcher polls the GitHub API for the prompt list, not a local
  git clone. The Dell may not have the repo cloned. API polling
  needs no clone and no credentials for a public repo.
  (Charles, Nov 24, 2026.)
- Config file holds the branch list. Default is the app-mode
  branch. Switch branches or watch several by editing the config,
  not the code. (Charles, Nov 24, 2026.)
- `--disable-approval` is on. The sandbox stays on. The watcher
  launches Muse Code headless, but Muse Code still cannot merge
  or publish without Charles's word. (Design settled Nov 23, 2026.)
- The seen-list lives on disk next to the script, not in the repo.
  The repo is shared; the seen-list is local to the Dell.
