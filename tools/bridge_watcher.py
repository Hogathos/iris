"""Bridge watcher: poll GitHub for new prompts, run Muse Code once each.

Runs on Charles's Dell at login. Polls the Iris repo for new
Bridge prompt files and runs Muse Code headless on each one, so
Charles does not paste prompts by hand. It is the automation
half of the Bridge: Main still writes the prompt and Charles
still checks it. The watcher only removes the paste.

Standard library only. No new dependencies: the Dell runs
plain Python 3.

Rules, enforced below:
- Never run the same prompt twice, even across a restart. The
  seen-list on disk is the guarantee.
- Never run prompts on a branch outside the config. Main is
  not special: it runs only if Charles lists it.
- Never merge, open pull requests, commit, or review. Only
  launch Muse Code.
- A network blip never kills the watcher. It skips and retries
  on the next poll.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# Defaults, used when the config file or a key is missing.
DEFAULT_BRANCHES = ["app-mode/packaging"]
DEFAULT_REPO = "Hogathos/iris"
DEFAULT_PROMPTS_DIR = "prompts"
DEFAULT_POLL_INTERVAL_SECONDS = 120
DEFAULT_STATE_FILENAME = "bridge_watcher_seen.json"
DEFAULT_LOG_FILENAME = "bridge_watcher.log"
DEFAULT_DOWNLOAD_DIRNAME = "bridge_prompts"

# GitHub API needs a user agent, or it answers 403.
_API_HEADERS = {"User-Agent": "iris-bridge-watcher/1.0",
                "Accept": "application/vnd.github+json"}

# How long one API call or download may take, in seconds.
_REQUEST_TIMEOUT = 60


class ConfigError(ValueError):
    """A config or seen-list file is unusable, in plain words.

    Raised, never guessed around: the message names the file
    and the bad value, and the watcher stops instead of
    running the wrong prompts.
    """


def _base_dir(config_path):
    """The folder relative paths resolve against: the config's own
    folder, or the script's folder when no config path is given.
    No separator normalization: the path is built from __file__
    as Python reports it, so it matches sibling lookups exactly."""
    if config_path:
        here = config_path
    else:
        here = __file__
    if not os.path.isabs(here):
        here = os.path.abspath(here)
    return os.path.dirname(here)


def _need_text(value, field, path):
    """A non-empty string, or a plain-words refusal."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"Bad {field} in {path}: it must be non-empty text.")
    return value


def load_config(path=None):
    """Read the JSON config file, filling defaults for anything
    missing. No file at all means first run: every default holds.
    Bad values raise ConfigError in plain words. Relative
    state, log, and download paths resolve against the config
    file's own folder.
    """
    raw = {}
    if path is not None and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"Bad config file {path}: it must hold one "
                f"JSON object ({exc}).") from exc
        if not isinstance(raw, dict):
            raise ConfigError(
                f"Bad config file {path}: it must hold one "
                f"JSON object, not a list or a bare value.")
    base = _base_dir(path)

    branches = raw.get("branches", list(DEFAULT_BRANCHES))
    if (not isinstance(branches, list) or not branches
            or any(not isinstance(b, str) or not b.strip()
                   for b in branches)):
        raise ConfigError(
            f"Bad branches in {path or 'defaults'}: it must be "
            f"a non-empty list of branch names.")
    repo = raw.get("repo", DEFAULT_REPO)
    _need_text(repo, "repo", path or "defaults")
    prompts_dir = raw.get("prompts_dir", DEFAULT_PROMPTS_DIR)
    _need_text(prompts_dir, "prompts_dir", path or "defaults")
    interval = raw.get("poll_interval_seconds",
                       DEFAULT_POLL_INTERVAL_SECONDS)
    if (isinstance(interval, bool) or not isinstance(interval,
                                                      (int, float))
            or not interval > 0):
        raise ConfigError(
            f"Bad poll interval in {path or 'defaults'}: it "
            f"must be a positive number of seconds.")
    state_file = raw.get("state_file", DEFAULT_STATE_FILENAME)
    _need_text(state_file, "state file path", path or "defaults")
    log_file = raw.get("log_file", DEFAULT_LOG_FILENAME)
    _need_text(log_file, "log file path", path or "defaults")
    download_dir = raw.get("download_dir", DEFAULT_DOWNLOAD_DIRNAME)
    _need_text(download_dir, "download folder path",
               path or "defaults")
    if not os.path.isabs(state_file):
        state_file = os.path.join(base, state_file)
    if not os.path.isabs(log_file):
        log_file = os.path.join(base, log_file)
    if not os.path.isabs(download_dir):
        download_dir = os.path.join(base, download_dir)
    return {
        "branches": branches,
        "repo": repo,
        "prompts_dir": prompts_dir,
        "poll_interval_seconds": interval,
        "state_file": state_file,
        "log_file": log_file,
        "download_dir": download_dir,
    }


def load_seen(path):
    """The seen filenames. A missing file means nothing seen yet.

    A corrupt file raises ConfigError naming the file. The
    watcher stops instead of guessing: re-running everything
    would run prompts twice, and skipping everything would drop
    work silently.
    """
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"Bad seen-list {path}: it must hold a JSON list "
            f"of file names ({exc}). Fix or delete the file, "
            f"then start the watcher again.") from exc
    if (not isinstance(raw, list)
            or any(not isinstance(name, str) for name in raw)):
        raise ConfigError(
            f"Bad seen-list {path}: it must hold a JSON list "
            f"of file names. Fix or delete the file, then "
            f"start the watcher again.")
    return set(raw)


def save_seen(path, seen):
    """Write the seen-list atomically, so a crash mid-write never
    leaves a half file behind."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    handle, tmp = tempfile.mkstemp(
        dir=parent, prefix=".seen-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(sorted(seen), out, indent=2)
            out.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def mark_seen(seen, name):
    """A new seen-set with one more file marked. The old set is
    untouched."""
    return set(seen) | {name}


def new_prompts(listed, seen):
    """The listed files never seen before, in listing order."""
    return [name for name in listed if name not in seen]


def is_watched(branch, branches):
    """True when this branch is in the config. Main is an
    ordinary name: configured means watched."""
    return branch in branches


def build_command(prompt_path):
    """The headless Muse Code command for one prompt file."""
    return ["muse", "exec", "--prompt-file", prompt_path,
            "--disable-approval"]


def log_launch(log_path, when, branch, filename, exit_code):
    """Append one launch line: time, branch, file, exit code."""
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(f"{when} branch={branch} file={filename} "
                     f"exit={exit_code}\n")


def github_list(repo, branch, prompts_dir, timeout=_REQUEST_TIMEOUT):
    """Prompt files on a branch, via the GitHub contents API.

    Returns [{"name":..., "url":...}]. Lets network errors rise:
    the caller treats them as a blip, not a death.
    """
    path = urllib.parse.quote(prompts_dir.strip("/"))
    url = (f"https://api.github.com/repos/{repo}/contents/"
           f"{path}?ref={urllib.parse.quote(branch)}")
    request = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.urlopen(request,
                                timeout=timeout) as resp:
        entries = json.load(resp)
    files = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") == "file":
            files.append({"name": entry.get("name", ""),
                          "url": entry.get("download_url", "")})
    return [item for item in files if item["name"] and item["url"]]


def download_file(url, dest_path, timeout=_REQUEST_TIMEOUT):
    """Fetch one file to disk, creating folders as needed."""
    parent = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(parent, exist_ok=True)
    request = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.urlopen(request,
                                timeout=timeout) as resp:
        body = resp.read()
    with open(dest_path, "wb") as handle:
        handle.write(body)


def run_muse(command, timeout=None):
    """Run Muse Code headless with a built command list.

    Takes the command list itself (see build_command), not a
    prompt path: run_once builds the command and hands it over.
    The timeout is in seconds; None waits as long as the run
    takes. Returns the exit code.
    """
    proc = subprocess.run(
        command, timeout=timeout,
        capture_output=True, text=True)
    return proc.returncode


def _is_baseline_run(state_file):
    """True when no seen-list file exists yet: the very first
    poll must baseline instead of running. An existing file,
    even an empty list, means baseline already happened."""
    return not os.path.exists(state_file)


def _baseline_branch(config, branch, files, now):
    """Mark every listed file seen without running any of them.

    Returns the names marked. Nothing is downloaded and
    nothing runs: old prompts stay done. The caller saves the
    seen-list once every watched branch listed cleanly.
    """
    names = [item["name"] for item in files if item.get("name")]
    parent = os.path.dirname(os.path.abspath(config["log_file"]))
    os.makedirs(parent, exist_ok=True)
    with open(config["log_file"], "a", encoding="utf-8") as handle:
        handle.write(f"{now} baseline branch={branch} "
                     f"files={len(names)} ran=none\n")
    return names


def run_once(config, lister, downloader, runner, now):
    """One poll: list, download the new, run each once, log it.

    The very first poll baselines instead of running: with no
    seen-list file yet, every file found is marked seen and
    nothing runs, so old prompts are never re-done. A later
    poll runs only files that appeared after the baseline.
    Only watched branches are listed. A listing failure skips
    that branch and retries next poll. A download failure leaves
    the file unmarked, so a later poll retries it. A Muse run
    is marked seen whatever its exit code: Iris never runs a
    prompt twice. Every launch is logged with time, branch,
    file, and exit code.
    """
    state_file = config["state_file"]
    baselining = _is_baseline_run(state_file)
    seen = load_seen(state_file)
    os.makedirs(config["download_dir"], exist_ok=True)
    launched = []
    skipped_branches = []
    failed_downloads = []
    baselined = []
    all_listed = True
    for branch in config["branches"]:
        if not is_watched(branch, config["branches"]):
            continue
        try:
            files = lister(branch)
        except Exception:
            skipped_branches.append(branch)
            all_listed = False
            continue
        if baselining:
            # First run: mark everything found, run nothing.
            # The save happens below, only when every watched
            # branch listed cleanly, so a blip keeps the next
            # poll in baseline mode instead of running old
            # prompts as if they were new.
            baselined.extend(_baseline_branch(
                config, branch, files, now))
            continue
        for item in files:
            name = item["name"]
            if name in seen:
                continue
            if os.path.basename(name) != name or name in (
                    ".", ".."):
                failed_downloads.append(name)
                continue
            dest = os.path.join(config["download_dir"], name)
            try:
                downloader(item["url"], dest)
            except Exception:
                failed_downloads.append(name)
                continue
            code = runner(build_command(dest))
            # Mark seen before logging: the no-rerun guarantee
            # beats a complete audit trail. A lost log line is
            # noise; a double run is a broken promise.
            seen = mark_seen(seen, name)
            save_seen(config["state_file"], seen)
            log_launch(config["log_file"], now, branch, name,
                       code)
            launched.append((branch, name, code))
    if baselining and all_listed:
        # Baseline complete, even when nothing was found: an
        # empty saved list still ends baseline mode, so a file
        # appearing later runs exactly once.
        for name in baselined:
            seen = mark_seen(seen, name)
        save_seen(state_file, seen)
    return {"launched": launched,
            "skipped_branches": skipped_branches,
            "failed_downloads": failed_downloads,
            "baselined": baselined if baselining else []}


def build_parser():
    """Command line: config path, plus --once for one poll."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--once", action="store_true",
                        help="run one poll, then stop")
    return parser


def main(argv=None):
    """Poll forever (or once with --once), surviving blips."""
    args = build_parser().parse_args(argv)
    if args.config is None:
        here = os.path.dirname(os.path.abspath(__file__))
        args.config = os.path.join(here,
                                   "bridge_watcher_config.json")
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"bridge watcher: {exc}", flush=True)
        return 2
    seen_path = config["state_file"]
    try:
        load_seen(seen_path)
    except ConfigError as exc:
        print(f"bridge watcher: {exc}", flush=True)
        return 2
    while True:
        try:
            run_once(
                config,
                lambda b: github_list(
                    config["repo"], b, config["prompts_dir"]),
                download_file, run_muse,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        except ConfigError as exc:
            print(f"bridge watcher: {exc}", flush=True)
            return 2
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001 - a poll must
            # never kill the watcher: log it and retry next poll.
            print(f"bridge watcher: poll failed ({exc}), "
                  f"retrying", flush=True)
        if args.once:
            return 0
        try:
            time.sleep(config["poll_interval_seconds"])
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
