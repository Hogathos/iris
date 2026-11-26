"""Bridge watcher: poll GitHub for new prompts, run each once.

Runs on Charles's Dell at login. Polls the Iris repo for new
Bridge prompt files and runs Muse Code or Codex headless, so
Charles does not paste prompts by hand. It is the automation
half of the Bridge: Main still writes the prompt and Charles
still checks it. The watcher only removes the paste.

Standard library only. No new dependencies: the Dell runs
plain Python 3.

Rules, enforced below:
- Never run the same prompt twice, even across a restart. The
  seen-list on disk is the guarantee.
- Never run an old prompt again: the first poll baselines every
  watched branch instead of running.
- Discovery only widens which branches are watched: the config
  list plus every branch GitHub returns. The run rules never
  change.
- Never merge, open pull requests, commit, or review. Only
  launch the agent, then push the branch it ran on.
- After each run, push that branch to origin by name. Never
  force-push. A failed push is logged and the poll goes on.
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


def _github_token():
    """The GitHub token from the environment, or empty when unset.

    Read on every call, so tests can fake it through os.environ
    and the Dell can set it without restarting anything else. An
    empty or whitespace-only value means no token: the caller
    behaves exactly as before, unauthenticated.
    """
    return os.environ.get("GITHUB_TOKEN", "").strip()


def _api_headers():
    """Base API headers plus bearer auth when a token is set.

    Used for api.github.com calls only. The raw file download
    never uses these headers, so the token never leaves the API.
    The token value is only placed on the outgoing request: it is
    never logged, never printed, and never written to a file.
    """
    headers = dict(_API_HEADERS)
    token = _github_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


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
    missing except repo_dir, which has no default and is
    required. A missing file or a missing or empty repo_dir
    raises ConfigError in plain words: the watcher stops
    instead of running Muse Code in the wrong folder. Bad
    values raise ConfigError in plain words. Relative state,
    log, download, and repo_dir paths resolve against the
    config file's own folder.
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
    repo_dir = _need_text(raw.get("repo_dir"), "repo_dir",
                            path or "defaults")
    if not os.path.isabs(repo_dir):
        repo_dir = os.path.join(base, repo_dir)
    return {
        "branches": branches,
        "repo": repo,
        "prompts_dir": prompts_dir,
        "poll_interval_seconds": interval,
        "state_file": state_file,
        "log_file": log_file,
        "download_dir": download_dir,
        "repo_dir": repo_dir,
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


def watched_branches(configured, discovered):
    """Every watched branch: the config list first, then each
    discovered branch not already listed, each once. Discovery
    widens the watched set: it never removes or reorders a
    config branch."""
    watched = list(configured)
    for name in discovered:
        if name not in watched:
            watched.append(name)
    return watched


def is_prompt_file(name):
    """True when the file is a runnable prompt: its name ends in
    -prompt.md. Any other file in the prompts dir (a report, a
    note, anything else) is simply not a prompt: it is never
    downloaded, never run, and never logged as a failure."""
    return (isinstance(name, str)
            and name.endswith("-prompt.md"))


def report_name(prompt_name):
    """The report file for a prompt: <name>-prompt.md keeps its
    report at <name>-report.md on the same branch. Anything that
    is not a prompt has no report."""
    suffix = "-prompt.md"
    if (isinstance(prompt_name, str)
            and prompt_name.endswith(suffix)):
        return prompt_name[:-len(suffix)] + "-report.md"
    return None


def build_command(prompt_path):
    """The headless Muse Code command for one prompt file."""
    return ["muse", "exec", "--prompt-file", prompt_path,
            "--disable-approval", "--trust-workspace"]


def build_codex_command(prompt_path):
    """Build the Windows command that sends a UTF-8 prompt to Codex."""
    # PowerShell treats straight and curly single quotes as delimiters.
    literal_path = prompt_path.translate({ord(quote): quote * 2 for quote in "'‘’‚‛"})
    script = (
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"Get-Content -LiteralPath '{literal_path}' -Raw -Encoding UTF8 -ErrorAction Stop | "
        "& codex -a never exec --sandbox workspace-write --ephemeral "
        "-m gpt-6.2-sol -; exit $LASTEXITCODE"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def log_launch(log_path, when, branch, filename, exit_code, agent="muse"):
    """Append one launch line with the time, branch, file, agent, and exit code."""
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(f"{when} branch={branch} file={filename} agent={agent} exit={exit_code}\n")


def github_list(repo, branch, prompts_dir, timeout=_REQUEST_TIMEOUT):
    """Prompt files on a branch, via the GitHub contents API.

    Returns [{"name":..., "url":...}]. Lets network errors rise:
    the caller treats them as a blip, not a death.
    """
    path = urllib.parse.quote(prompts_dir.strip("/"))
    url = (f"https://api.github.com/repos/{repo}/contents/"
           f"{path}?ref={urllib.parse.quote(branch)}")
    request = urllib.request.Request(url, headers=_api_headers())
    with urllib.request.urlopen(request,
                                timeout=timeout) as resp:
        entries = json.load(resp)
    files = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") == "file":
            files.append({"name": entry.get("name", ""),
                          "url": entry.get("download_url", "")})
    return [item for item in files if item["name"] and item["url"]]


def _next_branches_url(link_header):
    """The next page URL from a GitHub Link header, or None."""
    if not link_header:
        return None
    for part in link_header.split(","):
        pieces = [piece.strip() for piece in part.split(";")]
        if len(pieces) == 2 and pieces[1] == 'rel="next"':
            url = pieces[0]
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None


def github_list_branches(repo, timeout=_REQUEST_TIMEOUT):
    """Every branch name in the repo, via the GitHub branches API.

    Follows pages until no next page remains, so a repo with more
    branches than one page holds still returns them all. Lets
    network errors rise: the caller treats them as a blip, not a
    death.
    """
    names = []
    url = (f"https://api.github.com/repos/{repo}/branches"
           f"?per_page=100")
    while url:
        request = urllib.request.Request(url, headers=_api_headers())
        with urllib.request.urlopen(request,
                                    timeout=timeout) as resp:
            entries = json.load(resp)
            link = resp.headers.get("Link", "")
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("name"):
                names.append(entry["name"])
        url = _next_branches_url(link)
    return names


def download_file(url, dest_path, timeout=_REQUEST_TIMEOUT):
    """Fetch one file to disk, creating folders as needed."""
    parent = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(parent, exist_ok=True)
    # Base headers only: the token is for api.github.com, never for
    # raw downloads, so it is never sent here.
    request = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.urlopen(request,
                                timeout=timeout) as resp:
        body = resp.read()
    with open(dest_path, "wb") as handle:
        handle.write(body)


def run_muse(command, repo_dir, timeout=None):
    """Run Muse Code headless with a built command list.

    Takes the command list itself (see build_command), not a
    prompt path: run_once builds the command and hands it over.
    Runs in repo_dir, so Muse Code works in the iris repo and
    not in the watcher's own folder. Output streams to the
    watcher terminal: nothing is captured. The timeout is in
    seconds; None waits as long as the run takes. Returns the
    exit code.
    """
    proc = subprocess.run(
        command, timeout=timeout, cwd=repo_dir,
        shell=(os.name == "nt"))
    return proc.returncode


def run_codex(command, cwd):
    """Run Codex in the repo and stream its output to the terminal."""
    return subprocess.run(command, cwd=cwd).returncode


def get_current_branch(repo_dir):
    """The branch the working tree is on now, in plain words on failure.

    Raises when git cannot answer: the caller skips the run instead
    of guessing.
    """
    proc = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--abbrev-ref",
         "HEAD"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout,
            stderr=proc.stderr)
    return proc.stdout.strip()


def branch_exists_locally(repo_dir, branch):
    """True when refs/heads/branch exists in the local repo.

    It checks only the local ref. It never fetches. The repo
    is public, and fetch works without credentials on the Dell.
    """
    proc = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--verify",
         "--quiet", "refs/heads/" + branch],
        capture_output=True, text=True)
    return proc.returncode == 0


def fetch_branch(repo_dir, branch):
    """Fetch one branch from origin.

    It downloads objects and updates the remote-tracking ref.
    It never touches the working tree and never moves a local
    branch. It fetches just this branch, not the whole remote.
    It raises when git fails: the caller logs a plain skip.
    """
    branch_ref = "refs/heads/" + branch
    subprocess.run(
        ["git", "check-ref-format", branch_ref],
        capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", repo_dir, "fetch", "--no-tags",
         "--no-recurse-submodules",
         "--refmap=refs/heads/*:refs/remotes/origin/*",
         "origin", branch_ref],
        capture_output=True, text=True, check=True)


def fast_forward_branch(repo_dir, branch, *, already_checked_out=False):
    """Advance a local branch from its fetched origin ref.

    Merge updates the current branch and its working tree.
    A local fetch updates a branch that is not checked out.
    Git enforces fast-forward updates in both cases.
    """
    origin_ref = f"refs/remotes/origin/{branch}"
    if already_checked_out:
        # Merge treats a local branch ahead of origin as up to date.
        # Require HEAD to be at or behind the fetched commit.
        subprocess.run(
            ["git", "-C", repo_dir, "merge-base", "--is-ancestor",
             "HEAD", origin_ref],
            capture_output=True, text=True, check=True)
        subprocess.run(
            ["git", "-C", repo_dir, "merge", "--ff-only", "--no-squash",
             "--no-autostash", "--no-overwrite-ignore", "--", origin_ref],
            capture_output=True, text=True, check=True)
        return

    refspec = f"{origin_ref}:refs/heads/{branch}"
    subprocess.run(
        ["git", "-C", repo_dir, "fetch", "--no-tags",
         "--no-recurse-submodules", "--no-write-fetch-head",
         ".", refspec],
        capture_output=True, text=True, check=True)


def remote_branch_exists(repo_dir, branch):
    """True when refs/remotes/origin/branch exists locally.

    After a fetch, this is enough: git checkout branch creates
    the local tracking branch when no local branch exists but
    origin/branch does.
    """
    proc = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--verify",
         "--quiet", "refs/remotes/origin/" + branch],
        capture_output=True, text=True)
    return proc.returncode == 0


def working_tree_dirty(repo_dir):
    """True when git status shows uncommitted changes."""
    proc = subprocess.run(
        ["git", "-C", repo_dir, "status", "--porcelain"],
        capture_output=True, text=True, check=True)
    return bool(proc.stdout.strip())


def checkout_branch(repo_dir, branch):
    """Switch the working tree to a local branch. Raises when git
    cannot switch."""
    subprocess.run(
        ["git", "-C", repo_dir, "checkout", branch],
        capture_output=True, text=True, check=True)


def _prepare_branch_checkout(repo_dir, branch, *, already_checked_out):
    """Refresh a branch and check it out if needed, or return a skip reason."""
    try:
        fetch_branch(repo_dir, branch)
    except Exception:
        return f"fetch of {branch} failed, run skipped"

    if already_checked_out or branch_exists_locally(repo_dir, branch):
        try:
            fast_forward_branch(
                repo_dir, branch, already_checked_out=already_checked_out)
        except (OSError, subprocess.SubprocessError):
            return (f"cannot fast-forward {branch} to its origin head, "
                    "run skipped")
    elif not remote_branch_exists(repo_dir, branch):
        return f"no local branch {branch} or fetched branch, run skipped"

    if already_checked_out:
        return None
    try:
        checkout_branch(repo_dir, branch)
    except Exception as exc:
        return f"checkout to {branch} failed ({exc}), run skipped"
    return None


def push_branch(repo_dir, branch):
    """Push one branch to origin, naming it out loud.

    Runs ``git push origin <branch>`` in repo_dir. The watcher
    runs outside the sandbox as the user, so this push can use
    the credential helper where the sandboxed run cannot. Never
    force-pushes under any flag: a behind branch is refused by
    the remote, and the caller logs that and moves on. Raises
    when git fails: the caller logs it plainly instead of
    crashing or retrying.
    """
    subprocess.run(
        ["git", "-C", repo_dir, "push", "origin", branch],
        capture_output=True, text=True, check=True)


def _log_push(log_path, now, branch, outcome):
    """One plain-words push line: time, branch, what happened."""
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(f"{now} push branch={branch} {outcome}\n")


def _log_skip(log_path, now, branch, name, reason):
    """One plain-words skip line: time, branch, file, reason."""
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(f"{now} skip branch={branch} file={name} "
                     f"reason={reason}\n")


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


def _discovery_baseline_path(state_file):
    """The marker file: discovery baselined once, so the first poll
    after discovery lands marks every branch seen instead of
    running old prompts as if they were new."""
    return state_file + ".discovery-baselined"


def _mark_discovery_baselined(state_file):
    """Remember that discovery baselined once. Atomic, like the
    seen-list: a crash mid-write never leaves a half file."""
    path = _discovery_baseline_path(state_file)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    handle, tmp = tempfile.mkstemp(
        dir=parent, prefix=".seen-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write("discovery-baselined\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def run_once(config, lister, downloader, runner, now, branch_lister=None, *, codex_runner=None):
    """One poll: list, download the new, run each once, log it.

    The very first poll baselines instead of running: with no
    seen-list file yet, every file found is marked seen and
    nothing runs, so old prompts are never re-done. A later
    poll runs only files that appeared after the baseline.
    Only watched branches are listed: the config list plus, when
    a branch lister is given, every branch it returns. Discovery
    runs once per poll before the branch loop; a discovery
    failure skips discovery for that poll, is logged plainly,
    and the config branches still process. The first poll after
    discovery lands baselines every watched branch even when a
    seen-list already exists, so old prompts on newly found
    branches are marked seen instead of running. A listing
    failure skips that branch and retries next poll. Before
    running a prompt from branch B, fetch B and fast-forward
    its local branch if it exists. Switch to B if needed.
    A checkout skip leaves the prompt unmarked, so a later poll
    retries it. A download failure also leaves the file unmarked.
    A run is marked seen whatever its exit
    code: Iris never runs a prompt twice. Every launch is logged
    with time, branch, file, agent, and exit code. After each run the
    branch is pushed to origin and the push outcome is logged
    beside the run entries; a failed push never unmarks the run
    and never stops the poll.
    """
    if codex_runner is None:
        codex_runner = run_codex
    state_file = config["state_file"]
    baselining = _is_baseline_run(state_file)
    seen = load_seen(state_file)
    os.makedirs(config["download_dir"], exist_ok=True)
    launched = []
    skipped_branches = []
    failed_downloads = []
    skipped_checkouts = []
    skipped_reported = []
    baselined = []
    all_listed = True
    discovered = []
    if branch_lister is not None:
        try:
            discovered = [name for name in branch_lister()
                          if isinstance(name, str) and name.strip()]
        except Exception as exc:
            all_listed = False
            parent = os.path.dirname(
                os.path.abspath(config["log_file"]))
            os.makedirs(parent, exist_ok=True)
            with open(config["log_file"], "a",
                      encoding="utf-8") as handle:
                handle.write(f"{now} discover branches failed "
                             f"({exc}), config list only, "
                             f"retry next poll\n")
    watched = watched_branches(config["branches"], discovered)
    # The first poll with discovery baselines every watched branch,
    # even when a seen-list already exists from before discovery:
    # old prompts on newly found branches are marked seen instead
    # of running as if they were new.
    need_discovery_baseline = (
        branch_lister is not None
        and not os.path.exists(
            _discovery_baseline_path(state_file)))
    baseline_mode = baselining or need_discovery_baseline
    for branch in watched:
        try:
            files = lister(branch)
        except Exception:
            skipped_branches.append(branch)
            all_listed = False
            continue
        if baseline_mode:
            # First run: mark everything found, run nothing.
            # The save happens below, only when every watched
            # branch listed cleanly, so a blip keeps the next
            # poll in baseline mode instead of running old
            # prompts as if they were new.
            baselined.extend(_baseline_branch(
                config, branch, files, now))
            continue
        # The listing already holds every file in the prompts
        # dir, so the report check below needs no extra API call.
        listed_names = {item.get("name") for item in files
                        if item.get("name")}
        pending = [item for item in files
                   if item.get("name") not in seen
                   and os.path.basename(item.get("name", "")) ==
                   item.get("name", "")
                   and item.get("name") not in (".", "..")
                   and is_prompt_file(item.get("name", ""))
                   and report_name(item.get("name", ""))
                   not in listed_names]
        if pending and config.get("repo_dir"):
            repo_dir = config["repo_dir"]
            try:
                current = get_current_branch(repo_dir)
            except Exception as exc:
                reason = ("cannot read current branch, "
                          f"checkout to {branch} skipped ({exc})")
                for item in pending:
                    _log_skip(config["log_file"], now, branch,
                              item["name"], reason)
                    skipped_checkouts.append(
                        (branch, item["name"], reason))
                continue
            already_checked_out = current == branch
            if not already_checked_out:
                try:
                    dirty = working_tree_dirty(repo_dir)
                except Exception as exc:
                    reason = ("cannot check for uncommitted changes, "
                              f"checkout to {branch} skipped ({exc})")
                    for item in pending:
                        _log_skip(config["log_file"], now, branch,
                                  item["name"], reason)
                        skipped_checkouts.append(
                            (branch, item["name"], reason))
                    continue
                if dirty:
                    reason = ("uncommitted changes would be lost "
                              f"by checkout to {branch}, run skipped")
                    for item in pending:
                        _log_skip(config["log_file"], now, branch,
                                  item["name"], reason)
                        skipped_checkouts.append(
                            (branch, item["name"], reason))
                    continue
            reason = _prepare_branch_checkout(
                repo_dir, branch, already_checked_out=already_checked_out)
            if reason:
                for item in pending:
                    _log_skip(config["log_file"], now, branch,
                              item["name"], reason)
                    skipped_checkouts.append(
                        (branch, item["name"], reason))
                continue
        for item in files:
            name = item["name"]
            if name in seen:
                continue
            # Not a prompt, not a failure: skip it quietly, so a
            # report file or any other file never downloads, never
            # runs, and never triggers a checkout either.
            if not is_prompt_file(name):
                continue
            if os.path.basename(name) != name or name in (
                    ".", ".."):
                failed_downloads.append(name)
                continue
            # The prompt already ran (by hand or by an earlier
            # poll): its report is on the same branch. Mark it
            # seen and skip it in plain words, without downloading
            # or running it.
            expected = report_name(name)
            if expected in listed_names:
                reason = (f"report {expected} already exists, "
                          f"run skipped")
                seen = mark_seen(seen, name)
                save_seen(config["state_file"], seen)
                _log_skip(config["log_file"], now, branch, name,
                          reason)
                skipped_reported.append((branch, name, reason))
                continue
            agent = "codex" if name.endswith("-codex-prompt.md") else "muse"
            dest = os.path.join(config["download_dir"], name)
            try:
                downloader(item["url"], dest)
            except Exception:
                failed_downloads.append(name)
                continue
            if agent == "codex":
                code = codex_runner(build_codex_command(dest), config["repo_dir"])
            else:
                code = runner(build_command(dest))
            # Mark seen before logging: the no-rerun guarantee
            # beats a complete audit trail. A lost log line is
            # noise; a double run is a broken promise.
            seen = mark_seen(seen, name)
            save_seen(config["state_file"], seen)
            log_launch(config["log_file"], now, branch, name, code, agent=agent)
            launched.append((branch, name, code))
            # The run is done and marked seen above, whatever its
            # exit code. Push the branch it was on as a separate
            # step. A push failure never unmarks the run, never
            # re-runs it, never crashes the poll, and never
            # retries in a loop. A behind branch is logged and
            # left alone: no rebase, which is a separate prompt.
            if config.get("repo_dir"):
                try:
                    push_branch(config["repo_dir"], branch)
                except Exception as exc:
                    _log_push(config["log_file"], now, branch,
                              f"push failed ({exc})")
                else:
                    _log_push(config["log_file"], now, branch,
                              "pushed to origin")
    if baselining and all_listed:
        # Baseline complete, even when nothing was found: an
        # empty saved list still ends baseline mode, so a file
        # appearing later runs exactly once.
        for name in baselined:
            seen = mark_seen(seen, name)
        save_seen(state_file, seen)
    if need_discovery_baseline and all_listed:
        # Discovery baselined cleanly: later polls run only files
        # that appear after this poll. A poll that could not list
        # every branch leaves no marker, so the next poll stays in
        # baseline mode instead of running.
        _mark_discovery_baselined(state_file)
    return {"launched": launched,
            "skipped_branches": skipped_branches,
            "failed_downloads": failed_downloads,
            "skipped_checkouts": skipped_checkouts,
            "skipped_reported": skipped_reported,
            "discovered": discovered,
            "baselined": baselined if baseline_mode else []}


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
                download_file,
                lambda cmd: run_muse(cmd, config["repo_dir"]),
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                branch_lister=lambda: github_list_branches(
                    config["repo"]))
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
