# Bridge watcher — install and run (Dell)

The watcher polls the Iris repo for new Bridge prompt files and
runs Muse Code headless on each one, once. Charles installs it
once, by hand. Nothing here needs installing beyond Python 3:
the watcher is standard library only.

## Install

1. Put `bridge_watcher.py` and `bridge_watcher_config.json` in
   one folder on the Dell, for example
   `C:\Users\Charles\iris-bridge\`. Keep them together: the
   config, the seen-list, the log, and the downloaded prompts
   all live beside the script by default.
2. Check Python 3 runs: `python --version`.
3. Open Task Scheduler. Create a task that runs at login:
   action `python`, arguments the full path to
   `bridge_watcher.py`, start in the script folder. The
   watcher polls on its own timer from then on.
4. No `pip install` is needed. There are no dependencies.

## Switch branches or watch several

Edit `bridge_watcher_config.json`. Change the `branches`
list: replace the entry to switch branches, or add entries to
watch several at once. Never put `main` in unless you mean it:
the watcher runs whatever is listed. Restart the scheduled
task after editing. Unknown keys are ignored. Bad values stop
the watcher with a plain-words error naming the file.

Fields: `branches` (default `["app-mode/packaging"]`),
`repo` (`"Hogathos/iris"`), `prompts_dir` (`"prompts"`),
`poll_interval_seconds` (default 120), `state_file`,
`log_file`, `download_dir` (all default to files next to the
script; absolute paths stay where they are).

## Read the log

Open `bridge_watcher.log` next to the script. Every launch is
one line: time, branch, file, and exit code. A failed Muse
run is logged with its code and never runs again.

## Stop it

End the task in Task Scheduler, or kill the `python`
process running `bridge_watcher.py`. Stopping is safe: the
seen-list is already on disk, so nothing re-runs after a
restart. To run one poll by hand instead of the loop:
`python bridge_watcher.py --once`. To point at another
config: `python bridge_watcher.py --config <path>`.

## What it never does

It never merges, never opens a pull request, never commits,
never reviews. It only launches Muse Code with
`muse exec --prompt-file <path> --disable-approval`. The seen-list
and the log stay on the Dell. They are never committed.
