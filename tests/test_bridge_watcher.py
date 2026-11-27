"""Bridge watcher tests: seen-list, branch filter, config parsing.

The watcher polls the GitHub API for new prompt files and runs
Muse Code on them once each. These tests are the contract for
tools/bridge_watcher.py, which does not exist yet. They fail
until the module is implemented. The test writer is not the
implementer.

Rules under test (from specs/bridge-watcher/requirements.md):
- A prompt is never run twice, even across a restart.
- Branches outside the config are never run. Main is not
  watched unless Charles puts it in the config himself.
- The config carries defaults. Bad values are refused in
  plain words.
- Every launch is logged with time, branch, file, exit code.
- A network blip does not kill the watcher. It retries later.

Standard library only in the module under test.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import bridge_watcher as watcher


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class ConfigTest(unittest.TestCase):
    """Config parsing: defaults in, bad values out in plain words."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.missing = os.path.join(self.tmp.name, "no-such.json")

    def _config_path(self, payload):
        path = os.path.join(self.tmp.name, "watcher.json")
        if isinstance(payload, str):
            _write(path, payload)
        else:
            _write(path, json.dumps(payload))
        return path

    def test_missing_file_is_refused_without_repo_dir(self):
        """No config file means no repo_dir: the watcher stops
        instead of running Muse Code in the wrong folder."""
        with self.assertRaises(watcher.ConfigError) as ctx:
            watcher.load_config(self.missing)
        self.assertIn(self.missing, str(ctx.exception))

    def test_missing_keys_fall_back_to_defaults(self):
        """A partial config fills the gaps with defaults."""
        config = watcher.load_config(
            self._config_path({"branches": ["app-mode/x"],
                               "repo_dir": self.tmp.name}))
        self.assertEqual(config["branches"], ["app-mode/x"])
        self.assertEqual(config["poll_interval_seconds"], 120)
        self.assertEqual(config["repo"], "Hogathos/iris")
        self.assertEqual(config["repo_dir"], self.tmp.name)

    def test_no_config_at_all_is_refused_without_repo_dir(self):
        """No path means no repo_dir: the watcher stops instead
        of running Muse Code in the wrong folder."""
        with self.assertRaises(watcher.ConfigError):
            watcher.load_config(None)

    def test_bad_branches_are_refused_in_plain_words(self):
        """Branches must be a non-empty list of names."""
        for bad in ("app-mode/x", [], [""], [1], ["ok", ""],
                    ["ok", 7]):
            with self.assertRaises(watcher.ConfigError) as ctx:
                watcher.load_config(
                    self._config_path({"branches": bad}))
            self.assertIn("branch", str(ctx.exception).lower())

    def test_bad_interval_is_refused_in_plain_words(self):
        """The poll interval must be a positive number."""
        for bad in (0, -5, "120", True, None, [120]):
            with self.assertRaises(watcher.ConfigError) as ctx:
                watcher.load_config(self._config_path(
                    {"poll_interval_seconds": bad}))
            self.assertIn("poll", str(ctx.exception).lower())

    def test_bad_repo_or_dir_is_refused_in_plain_words(self):
        """Repo and prompts dir must be non-empty text."""
        for field in ("repo", "prompts_dir"):
            for bad in ("", "  ", 7, None):
                with self.assertRaises(watcher.ConfigError) as ctx:
                    watcher.load_config(
                        self._config_path({field: bad}))
                self.assertIn(field.split("_")[0],
                              str(ctx.exception).lower())

    def test_bad_paths_are_refused_in_plain_words(self):
        """State, log, and download paths must be non-empty text."""
        for field in ("state_file", "log_file", "download_dir"):
            with self.assertRaises(watcher.ConfigError) as ctx:
                watcher.load_config(self._config_path({field: ""}))
            self.assertIn("path", str(ctx.exception).lower())

    def test_non_object_config_is_refused_in_plain_words(self):
        """A config file must hold one JSON object."""
        with self.assertRaises(watcher.ConfigError):
            watcher.load_config(self._config_path([1, 2]))

    def test_broken_json_is_refused_in_plain_words(self):
        """A file that is not JSON is refused, never guessed."""
        with self.assertRaises(watcher.ConfigError):
            watcher.load_config(self._config_path("{nope"))


class SeenListTest(unittest.TestCase):
    """The seen-list: never run a prompt twice, even past a reboot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "seen.json")

    def test_missing_seen_file_means_nothing_seen(self):
        """A first run starts with an empty seen-list."""
        self.assertEqual(watcher.load_seen(self.path), set())

    def test_save_and_load_round_trip(self):
        """What is saved is what comes back, across a restart."""
        watcher.save_seen(self.path, {"a.md", "b.md"})
        self.assertEqual(
            watcher.load_seen(self.path), {"a.md", "b.md"})

    def test_mark_seen_never_forgets(self):
        """Marking adds without dropping what was there."""
        seen = watcher.mark_seen({"a.md"}, "b.md")
        self.assertEqual(seen, {"a.md", "b.md"})

    def test_new_prompts_skips_seen_files(self):
        """Seen files never run again. Order is kept."""
        listed = ["a.md", "b.md", "c.md"]
        self.assertEqual(
            watcher.new_prompts(listed, {"b.md"}),
            ["a.md", "c.md"])

    def test_corrupt_seen_file_is_refused_not_guessed(self):
        """A broken seen-list stops the watcher instead of
        re-running everything or skipping everything unseen."""
        _write(self.path, "{corrupt")
        with self.assertRaises(watcher.ConfigError) as ctx:
            watcher.load_seen(self.path)
        self.assertIn(os.path.basename(self.path),
                      str(ctx.exception))
        _write(self.path, json.dumps({"not": "a list"}))
        with self.assertRaises(watcher.ConfigError):
            watcher.load_seen(self.path)


class BranchFilterTest(unittest.TestCase):
    """Only watched branches run. Main is not special."""

    def test_watched_branch_runs(self):
        self.assertTrue(
            watcher.is_watched("app-mode/x", ["app-mode/x"]))

    def test_unwatched_branch_never_runs(self):
        self.assertFalse(
            watcher.is_watched("app-mode/y", ["app-mode/x"]))

    def test_main_runs_only_when_configured(self):
        """Main is an ordinary name: configured means watched."""
        self.assertFalse(watcher.is_watched("main", ["app-mode/x"]))
        self.assertTrue(watcher.is_watched("main", ["main"]))


class LaunchCommandTest(unittest.TestCase):
    """The Muse Code command line, exact."""

    def test_build_command_runs_muse_headless(self):
        self.assertEqual(
            watcher.build_command("/tmp/p.md"),
            ["muse", "exec", "--prompt-file", "/tmp/p.md",
             "--disable-approval", "--trust-workspace"])

    def test_build_command_trusts_workspace(self):
        """The watcher runs the user's own repo, so the command
        trusts the workspace: project rules, delegation, and
        project MCP servers load instead of being skipped."""
        self.assertIn("--trust-workspace",
                      watcher.build_command("/tmp/p.md"))


class RunOnceTest(unittest.TestCase):
    """One poll: run what is new, log it, survive blips."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.config = {
            "branches": ["app-mode/x"],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
        }
        self.ran = []
        self.saved = {}

    def _lister(self, files):
        def listing(branch):
            assert branch == "app-mode/x"
            return [{"name": name,
                     "url": "https://example.invalid/" + name}
                    for name in files]
        return listing

    def _downloader(self):
        def download(url, dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt for " + url)
            self.saved[dest] = url
        return download

    def _runner(self, code=0):
        def run(cmd):
            self.ran.append(cmd)
            return code
        return run

    def test_only_new_prompts_run_and_are_logged(self):
        """A new file runs once, is logged, and is marked seen."""
        watcher.save_seen(self.state, {"old-prompt.md"})
        report = watcher.run_once(
            self.config,
            self._lister(["old-prompt.md", "new-prompt.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(
            self.ran[0],
            ["muse", "exec", "--prompt-file",
             os.path.join(self.downloads, "new-prompt.md"),
             "--disable-approval", "--trust-workspace"])
        with open(self.log, encoding="utf-8") as handle:
            logged = handle.read()
        self.assertIn("2026-11-24T00:00:00Z", logged)
        self.assertIn("app-mode/x", logged)
        self.assertIn("new-prompt.md", logged)
        self.assertIn("0", logged)
        self.assertEqual(report["launched"],
                         [("app-mode/x", "new-prompt.md", 0)])
        self.assertEqual(watcher.load_seen(self.state),
                         {"old-prompt.md", "new-prompt.md"})

    def test_second_poll_runs_nothing(self):
        """The seen-list survives: a rerun finds no work."""
        watcher.save_seen(self.state, set())
        watcher.run_once(
            self.config, self._lister(["new-prompt.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        second = watcher.run_once(
            self.config, self._lister(["new-prompt.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(second["launched"], [])

    def test_failed_run_is_still_marked_seen(self):
        """A failed Muse run is logged with its code and never
        runs again. Iris never double-runs a prompt."""
        watcher.save_seen(self.state, set())
        report = watcher.run_once(
            self.config, self._lister(["new-prompt.md"]),
            self._downloader(), self._runner(code=3),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"],
                         [("app-mode/x", "new-prompt.md", 3)])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn("3", handle.read().splitlines()[-1])
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})

    def test_unwatched_branch_is_never_listed(self):
        """The listing is only asked for watched branches."""
        def listing(branch):
            raise AssertionError(
                "must not list " + branch)
        report = watcher.run_once(
            dict(self.config, branches=[]),
            listing, self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(self.ran, [])
        self.assertEqual(report["launched"], [])

    def test_network_blip_kills_nothing(self):
        """A listing failure is skipped, and the next poll
        retries. The watcher keeps going."""
        def listing(branch):
            raise ConnectionError("blip")
        report = watcher.run_once(
            self.config, listing, self._downloader(),
            self._runner(), now="2026-11-24T00:00:00Z")
        self.assertEqual(self.ran, [])
        self.assertEqual(report["skipped_branches"], ["app-mode/x"])

    def test_failed_download_retries_next_poll(self):
        """A file that cannot download is not marked seen, so a
        later poll retries it. The other new file still runs."""
        watcher.save_seen(self.state, set())

        def download(url, dest):
            if url.endswith("bad-prompt.md"):
                raise ConnectionError("blip")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")
        report = watcher.run_once(
            self.config,
            self._lister(["bad-prompt.md", "good-prompt.md"]),
            download, self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertIn("good-prompt.md", self.ran[0][3])
        self.assertEqual(watcher.load_seen(self.state),
                         {"good-prompt.md"})
        self.assertEqual(report["failed_downloads"],
                         ["bad-prompt.md"])


class PromptReportSkipTest(unittest.TestCase):
    """run_once runs only *-prompt.md, and skips reported prompts.

    Only a file ending in -prompt.md is runnable. A prompt whose
    report (<name>-report.md) is already listed on the same branch
    is done: it is marked seen and skipped, never downloaded or
    run. Any other file is simply not a prompt.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.config = {
            "branches": ["app-mode/x"],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
        }
        self.ran = []
        self.downloaded = []

    def _lister(self, files):
        def listing(branch):
            assert branch == "app-mode/x"
            return [{"name": name,
                     "url": "https://example.invalid/" + name}
                    for name in files]
        return listing

    def _downloader(self):
        def download(url, dest):
            self.downloaded.append(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt for " + url)
        return download

    def _runner(self, code=0):
        def run(cmd):
            self.ran.append(cmd)
            return code
        return run

    def _read_log(self):
        with open(self.log, encoding="utf-8") as handle:
            return handle.read()

    def test_report_file_is_never_downloaded_or_run(self):
        """A *-report.md file is not a prompt: no download, no run,
        no launch entry, and no checkout."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                side_effect=AssertionError("no checkout expected")), \
            mock.patch.object(watcher, "checkout_branch",
                              side_effect=AssertionError(
                                  "no checkout expected")):
            report = watcher.run_once(
                dict(self.config,
                     repo_dir=os.path.join(self.tmp.name, "repo")),
                self._lister(["job-report.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(self.downloaded, [])
        self.assertEqual(self.ran, [])
        self.assertEqual(report["launched"], [])
        self.assertEqual(report["failed_downloads"], [])
        self.assertEqual(report["skipped_reported"], [])
        self.assertEqual(watcher.load_seen(self.state), set())

    def test_file_without_prompt_suffix_never_runs(self):
        """A file with no -prompt.md suffix is never downloaded
        or run, and never logged as a failure."""
        watcher.save_seen(self.state, set())
        report = watcher.run_once(
            self.config, self._lister(["notes.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(self.downloaded, [])
        self.assertEqual(self.ran, [])
        self.assertEqual(report["launched"], [])
        self.assertEqual(report["failed_downloads"], [])
        self.assertEqual(report["skipped_reported"], [])

    def test_prompt_with_report_is_skipped(self):
        """A prompt whose report is listed is skipped: not
        downloaded, not run, marked seen, logged in plain words,
        and listed in skipped_reported."""
        watcher.save_seen(self.state, set())
        report = watcher.run_once(
            self.config,
            self._lister(["job-prompt.md", "job-report.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(self.downloaded, [])
        self.assertEqual(self.ran, [])
        self.assertEqual(report["launched"], [])
        self.assertEqual(watcher.load_seen(self.state),
                         {"job-prompt.md"})
        logged = self._read_log().lower()
        self.assertIn("skip", logged)
        self.assertIn("job-prompt.md", self._read_log())
        self.assertEqual(len(report["skipped_reported"]), 1)
        branch, name, reason = report["skipped_reported"][0]
        self.assertEqual((branch, name), ("app-mode/x",
                                          "job-prompt.md"))
        self.assertIn("job-report.md", reason)

    def test_prompt_without_report_runs_as_normal(self):
        """A prompt with no report in the listing runs as normal."""
        watcher.save_seen(self.state, set())
        report = watcher.run_once(
            self.config, self._lister(["solo-prompt.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertIn("solo-prompt.md", self.ran[0][3])
        self.assertEqual(report["launched"],
                         [("app-mode/x", "solo-prompt.md", 0)])
        self.assertEqual(report["skipped_reported"], [])
        self.assertEqual(watcher.load_seen(self.state),
                         {"solo-prompt.md"})

    def test_reported_only_pending_triggers_no_checkout(self):
        """When the only pending prompt is already reported, no
        checkout is attempted and the runner is never called. The
        prompt is still marked seen."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        config = dict(
            self.config,
            repo_dir=os.path.join(self.tmp.name, "repo"))
        with mock.patch.object(
                watcher, "get_current_branch",
                side_effect=AssertionError(
                    "no checkout expected")) as current, \
            mock.patch.object(
                watcher, "working_tree_dirty") as dirty, \
            mock.patch.object(
                watcher, "branch_exists_locally") as exists, \
            mock.patch.object(
                watcher, "checkout_branch") as checkout:
            report = watcher.run_once(
                config,
                self._lister(["job-prompt.md", "job-report.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        current.assert_not_called()
        dirty.assert_not_called()
        exists.assert_not_called()
        checkout.assert_not_called()
        self.assertEqual(self.ran, [])
        self.assertEqual(self.downloaded, [])
        self.assertEqual(report["launched"], [])
        self.assertEqual(len(report["skipped_reported"]), 1)
        self.assertEqual(watcher.load_seen(self.state),
                         {"job-prompt.md"})

    def test_second_poll_runs_nothing_after_report_skip(self):
        """A prompt skipped for its report is in the seen-list now,
        so the next poll runs nothing."""
        watcher.save_seen(self.state, set())
        listing = self._lister(["job-prompt.md", "job-report.md"])
        first = watcher.run_once(
            self.config, listing,
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(len(first["skipped_reported"]), 1)
        second = watcher.run_once(
            self.config, listing,
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z")
        self.assertEqual(self.ran, [])
        self.assertEqual(second["launched"], [])
        self.assertEqual(second["skipped_reported"], [])


class RunMuseCommandTest(unittest.TestCase):
    """run_muse takes a command list, not a prompt path (Bug 1).

    Regression: run_once passes a built command list, but the
    old run_muse built a second command around it, nesting the
    list, and subprocess raised TypeError. The runner contract
    (a command list) is correct and stays.
    """

    def test_run_muse_runs_command_list_directly(self):
        from unittest import mock

        seen = []

        def _fake_run(cmd, **kwargs):
            for part in cmd:
                if not isinstance(part, (str, bytes, os.PathLike)):
                    raise TypeError(
                        "expected str, bytes or os.PathLike "
                        f"object, not {type(part).__name__}")
            seen.append((cmd, dict(kwargs)))
            return mock.Mock(returncode=3)

        cmd = ["muse", "exec", "--prompt-file", "p.md",
               "--disable-approval"]
        with mock.patch.object(watcher.subprocess, "run",
                               side_effect=_fake_run):
            code = watcher.run_muse(cmd, "/tmp/iris-repo")
        self.assertEqual(code, 3)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], cmd)


class RunMuseShellTest(unittest.TestCase):
    """run_muse launches muse.cmd on Windows (Bug 3).

    Regression: the watcher runs on Windows where Muse Code
    is installed as muse.cmd, not muse.exe. With the default
    shell=False, subprocess uses CreateProcess, which cannot
    run .cmd files, so every poll failed with FileNotFoundError
    ([WinError 2]) and retried forever without running a prompt.
    run_muse must pass shell=True on Windows (os.name == "nt")
    so the command runs through cmd.exe, and shell=False
    elsewhere so behaviour is unchanged.
    """

    def _run_and_capture_shell(self, target_os):
        """Run run_muse with os.name patched, return (code, shell).

        Mocks watcher.subprocess.run so nothing launches, and
        patches watcher.os.name so the test reads the same on
        any platform. Captures the shell kwarg it was called with.
        """
        from unittest import mock

        seen = {}

        def _fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return mock.Mock(returncode=0)

        cmd = ["muse", "exec", "--prompt-file", "p.md",
               "--disable-approval"]
        with mock.patch.object(watcher.subprocess, "run",
                               side_effect=_fake_run):
            with mock.patch.object(watcher.os, "name", target_os):
                code = watcher.run_muse(cmd, "/tmp/iris-repo")
        return code, seen.get("shell")

    def test_windows_runs_through_shell(self):
        """On Windows the command runs through cmd.exe."""
        code, shell = self._run_and_capture_shell("nt")
        self.assertEqual(code, 0)
        self.assertEqual(shell, True)

    def test_other_platforms_run_without_shell(self):
        """Off Windows the command runs directly, as before."""
        code, shell = self._run_and_capture_shell("posix")
        self.assertEqual(code, 0)
        self.assertEqual(shell, False)


class RunMuseOutputCwdTest(unittest.TestCase):
    """run_muse streams output and runs in the repo dir.

    Regression: run_muse captured all of Muse Code's stdout and
    stderr with capture_output=True and never used it, so the
    watcher terminal showed nothing. It also ran with no cwd, so
    Muse Code ran in the watcher's own folder instead of the
    iris repo and did no work.
    """

    def _run_and_capture_kwargs(self, repo_dir):
        """Run run_muse mocked, return (code, kwargs)."""
        from unittest import mock

        seen = {}

        def _fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return mock.Mock(returncode=0)

        cmd = ["muse", "exec", "--prompt-file", "p.md",
               "--disable-approval"]
        with mock.patch.object(watcher.subprocess, "run",
                               side_effect=_fake_run):
            code = watcher.run_muse(cmd, repo_dir)
        return code, seen

    def test_output_streams_to_terminal(self):
        """No capture_output: output inherits the terminal."""
        code, kwargs = self._run_and_capture_kwargs("/tmp/iris-repo")
        self.assertEqual(code, 0)
        self.assertNotIn("capture_output", kwargs)

    def test_runs_in_repo_dir(self):
        """subprocess runs with cwd equal to repo_dir."""
        repo_dir = os.path.join("bridge", "repo")
        code, kwargs = self._run_and_capture_kwargs(repo_dir)
        self.assertEqual(code, 0)
        self.assertEqual(kwargs.get("cwd"), repo_dir)


class RepoDirConfigTest(unittest.TestCase):
    """load_config requires repo_dir, resolved like the rest."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _config_path(self, payload):
        path = os.path.join(self.tmp.name, "watcher.json")
        _write(path, json.dumps(payload))
        return path

    def test_missing_repo_dir_is_refused_naming_file(self):
        """No repo_dir stops the watcher, naming the file."""
        path = self._config_path({"branches": ["app-mode/x"]})
        with self.assertRaises(watcher.ConfigError) as ctx:
            watcher.load_config(path)
        self.assertIn(path, str(ctx.exception))

    def test_empty_repo_dir_is_refused_naming_file(self):
        """An empty repo_dir stops the watcher, naming the file."""
        for bad in ("", "  ", None, 7):
            path = self._config_path({"repo_dir": bad})
            with self.assertRaises(watcher.ConfigError) as ctx:
                watcher.load_config(path)
            self.assertIn(path, str(ctx.exception))

    def test_relative_repo_dir_resolves_against_config_folder(self):
        """A relative repo_dir joins the config file's own folder,
        the same as the state, log, and download paths."""
        path = self._config_path({"repo_dir": "myrepo"})
        config = watcher.load_config(path)
        self.assertEqual(config["repo_dir"],
                         os.path.join(self.tmp.name, "myrepo"))

    def test_absolute_repo_dir_stays_where_it_is(self):
        """An absolute repo_dir is kept as is."""
        here = os.path.abspath(self.tmp.name)
        path = self._config_path({"repo_dir": here})
        config = watcher.load_config(path)
        self.assertEqual(config["repo_dir"], here)


class BaselineTest(unittest.TestCase):
    """First run baselines: mark everything, run nothing (Bug 2).

    Regression: the first poll ran every old prompt file. Now
    the first poll, with no seen-list file yet, only baselines.
    A file appearing later runs exactly once.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.config = {
            "branches": ["app-mode/x"],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
        }
        self.ran = []
        self.downloaded = []

    def _lister(self, files):
        def listing(branch):
            assert branch == "app-mode/x"
            return [{"name": name,
                     "url": "https://example.invalid/" + name}
                    for name in files]
        return listing

    def _downloader(self):
        def download(url, dest):
            self.downloaded.append(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")
        return download

    def test_first_run_marks_seen_without_running(self):
        """No seen-list file: both files marked, neither runs,
        the log says baseline, the next identical poll is quiet,
        and a file appearing later runs exactly once."""
        self.assertFalse(os.path.exists(self.state))
        first = watcher.run_once(
            self.config, self._lister(["a-prompt.md", "b-prompt.md"]),
            self._downloader(),
            lambda cmd: self.ran.append(cmd) or 0,
            now="2026-11-24T00:00:00Z")
        self.assertEqual(self.ran, [])
        self.assertEqual(self.downloaded, [])
        self.assertEqual(watcher.load_seen(self.state),
                         {"a-prompt.md", "b-prompt.md"})
        self.assertEqual(first["launched"], [])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn("baseline", handle.read().lower())
        second = watcher.run_once(
            self.config, self._lister(["a-prompt.md", "b-prompt.md"]),
            self._downloader(),
            lambda cmd: self.ran.append(cmd) or 0,
            now="2026-11-24T00:01:00Z")
        self.assertEqual(self.ran, [])
        self.assertEqual(second["launched"], [])
        third = watcher.run_once(
            self.config,
            self._lister(["a-prompt.md", "b-prompt.md", "c-prompt.md"]),
            self._downloader(),
            lambda cmd: self.ran.append(cmd) or 0,
            now="2026-11-24T00:02:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertIn("c-prompt.md", self.ran[0][3])
        self.assertEqual(third["launched"],
                         [("app-mode/x", "c-prompt.md", 0)])


class BranchCheckoutTest(unittest.TestCase):
    """run_once checks out the prompt branch before running (Bug 6).

    Regression: the watcher ran Muse Code in repo_dir as-is, without
    checking out the prompt branch first. The spec and tests on the
    prompt branch were missing, so Muse implemented from the prompt
    text alone. run_once must switch the working tree to branch B
    before running a prompt from B, with safeguards that skip the
    run and leave the prompt unmarked.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.repo_dir = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo_dir, exist_ok=True)
        self.branch = "feature/b"
        self.config = {
            "branches": [self.branch],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
            "repo_dir": self.repo_dir,
        }
        self.order = []

    def _lister(self, files):
        def listing(branch):
            self.assertEqual(branch, self.branch)
            return [{"name": name,
                     "url": "https://example.invalid/" + name}
                    for name in files]
        return listing

    def _downloader(self):
        def download(url, dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")
        return download

    def _runner(self, code=0):
        def run(cmd):
            self.order.append("run")
            return code
        return run

    def _read_log(self):
        with open(self.log, encoding="utf-8") as handle:
            return handle.read()

    def test_checkout_happens_before_run_on_other_branch(self):
        """A tree on another branch is switched before the run."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch") as current, \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                return_value=False), \
            mock.patch.object(watcher, "fetch_branch"), \
            mock.patch.object(
                watcher, "remote_branch_exists",
                return_value=True), \
            mock.patch.object(
                watcher, "checkout_branch",
                side_effect=lambda d, b: self.order.append("checkout")):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(current.call_args[0][0], self.repo_dir)
        self.assertEqual(self.order, ["checkout", "run"])
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})

    def test_no_checkout_when_already_on_branch(self):
        """A tree already on B runs with no checkout."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value=self.branch), \
            mock.patch.object(watcher, "fetch_branch", return_value=None), \
            mock.patch.object(watcher, "fast_forward_branch", return_value=None), \
            mock.patch.object(watcher, "branch_exists_locally", return_value=True), \
            mock.patch.object(watcher, "working_tree_dirty", return_value=False), \
            mock.patch.object(watcher, "checkout_branch") as checkout:
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        checkout.assert_not_called()
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})

    def test_dirty_tree_skips_run_and_leaves_unmarked(self):
        """Uncommitted changes that a checkout would lose skip the
        run. The prompt stays unmarked for a later poll."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=True), \
            mock.patch.object(watcher, "checkout_branch") as checkout:
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        checkout.assert_not_called()
        self.assertEqual(report["launched"], [])
        self.assertEqual(watcher.load_seen(self.state), set())
        logged = self._read_log().lower()
        self.assertIn("uncommitted", logged)
        self.assertIn(self.branch.lower(), logged)

    def test_missing_local_branch_skips_run_and_leaves_unmarked(self):
        """A branch with no local copy skips the run. No fetch: the
        watcher has no credentials for that. The prompt stays
        unmarked."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                return_value=False), \
            mock.patch.object(watcher, "checkout_branch") as checkout:
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        checkout.assert_not_called()
        self.assertEqual(report["launched"], [])
        self.assertEqual(watcher.load_seen(self.state), set())
        logged = self._read_log().lower()
        self.assertIn(self.branch.lower(), logged)

    def test_failed_checkout_skips_run_and_leaves_unmarked(self):
        """A checkout that fails skips the run. The prompt stays
        unmarked for a later poll."""
        from unittest import mock
        import subprocess

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                return_value=False), \
            mock.patch.object(watcher, "fetch_branch"), \
            mock.patch.object(
                watcher, "remote_branch_exists",
                return_value=True), \
            mock.patch.object(
                watcher, "checkout_branch",
                side_effect=subprocess.CalledProcessError(1, "git")):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"], [])
        self.assertEqual(watcher.load_seen(self.state), set())
        logged = self._read_log().lower()
        self.assertIn("checkout", logged)
        self.assertIn(self.branch.lower(), logged)


class FetchMissingBranchTest(unittest.TestCase):
    """run_once fetches a branch missing locally before running it.

    Regression: on Nov 24 the watcher skipped a prompt with
    ``no local branch <branch>, no fetch attempted`` when the
    branch existed on GitHub but the Dell had never checked it
    out. The watcher now runs ``git fetch origin <branch>`` on
    the missing-branch path, checks out, and runs. A fetch
    failure is a plain skip, never a crash. A branch still
    missing after a fetch is skipped as before.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.repo_dir = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo_dir, exist_ok=True)
        self.branch = "feature/b"
        self.config = {
            "branches": [self.branch],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
            "repo_dir": self.repo_dir,
        }
        self.order = []

    def _lister(self, files):
        def listing(branch):
            self.assertEqual(branch, self.branch)
            return [{"name": name,
                     "url": "https://example.invalid/" + name}
                    for name in files]
        return listing

    def _downloader(self):
        def download(url, dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")
        return download

    def _runner(self, code=0):
        def run(cmd):
            self.order.append("run")
            return code
        return run

    def _read_log(self):
        with open(self.log, encoding="utf-8") as handle:
            return handle.read()

    def test_fetchable_remote_branch_is_fetched_checked_out_and_run(self):
        """No local branch, but the fetch brings the branch down:
        the watcher fetches just that branch, checks it out, and
        runs the prompt exactly once."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                side_effect=[False, False]), \
            mock.patch.object(
                watcher, "remote_branch_exists",
                return_value=True), \
            mock.patch.object(
                watcher, "fetch_branch",
                side_effect=lambda d, b: self.order.append(
                    ("fetch", d, b))), \
            mock.patch.object(
                watcher, "checkout_branch",
                side_effect=lambda d, b: self.order.append(
                    ("checkout", d, b))):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        fetches = [entry for entry in self.order
                   if isinstance(entry, tuple) and entry[0] == "fetch"]
        self.assertEqual(
            fetches, [("fetch", self.repo_dir, self.branch)])
        self.assertEqual(self.order[-2:], [
            ("checkout", self.repo_dir, self.branch), "run"])
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(watcher.load_seen(self.state), {"new-prompt.md"})

    def test_fetch_failure_is_a_plain_skip(self):
        """A failing fetch never crashes the poll. The prompt is
        not run, not marked seen, logged in plain words naming
        the fetch failure, and listed in skipped_checkouts."""
        from unittest import mock
        import subprocess

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                return_value=False), \
            mock.patch.object(
                watcher, "fetch_branch",
                side_effect=subprocess.CalledProcessError(
                    1, ["git", "fetch"])), \
            mock.patch.object(watcher, "checkout_branch") as checkout:
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        checkout.assert_not_called()
        self.assertEqual(self.order, [])
        self.assertEqual(report["launched"], [])
        self.assertEqual(watcher.load_seen(self.state), set())
        self.assertTrue(report["skipped_checkouts"])
        logged = self._read_log().lower()
        self.assertIn("fetch", logged)
        self.assertIn(self.branch.lower(), logged)
        reasons = " ".join(
            reason for _, _, reason in report["skipped_checkouts"])
        self.assertIn("fetch", reasons.lower())

    def test_branch_still_missing_after_fetch_is_skipped_as_before(self):
        """A successful fetch that still leaves no branch behind
        skips with the existing reason. Nothing runs, nothing is
        marked seen."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                side_effect=[False, False]), \
            mock.patch.object(
                watcher, "remote_branch_exists",
                return_value=False), \
            mock.patch.object(
                watcher, "fetch_branch",
                return_value=None), \
            mock.patch.object(watcher, "checkout_branch") as checkout:
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        checkout.assert_not_called()
        self.assertEqual(report["launched"], [])
        self.assertEqual(watcher.load_seen(self.state), set())
        logged = self._read_log().lower()
        self.assertIn("no local branch", logged)
        self.assertIn(self.branch.lower(), logged)

    def test_second_poll_runs_nothing_after_fetched_run(self):
        """A prompt fetched and run this way is in the seen-list
        now, so the next identical poll runs nothing."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                side_effect=[False, False]), \
            mock.patch.object(
                watcher, "remote_branch_exists",
                return_value=True), \
            mock.patch.object(
                watcher, "fetch_branch",
                return_value=None), \
            mock.patch.object(
                watcher, "checkout_branch",
                return_value=None):
            first = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(first["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        second = watcher.run_once(
            self.config, self._lister(["new-prompt.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z")
        self.assertEqual(second["launched"], [])
        self.assertEqual(len(self.order), 1)
        self.assertEqual(watcher.load_seen(self.state), {"new-prompt.md"})


class DiscoveryTest(unittest.TestCase):
    """run_once discovers prompt branches, with no cascade.

    Discovery lists every branch through a branch lister and
    watches the union of the config list and the discovered
    branches. The anti-cascade rules hold for discovered branches
    exactly as for config branches: only *-prompt.md files run,
    a prompt with a report is skipped and marked seen, and the
    seen-list is global. The first poll baselines every branch
    instead of running. A branch-listing failure skips discovery
    for that poll and the config branches still process.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.config = {
            "branches": ["app-mode/x"],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
        }
        self.listed = []
        self.downloaded = []
        self.ran = []

    def _files(self, mapping):
        def listing(branch):
            self.listed.append(branch)
            return [{"name": name,
                     "url": "https://example.invalid/"
                     + branch + "/" + name}
                    for name in mapping.get(branch, [])]
        return listing

    def _branches(self, branches):
        def branch_listing():
            return list(branches)
        return branch_listing

    def _failing_branches(self):
        def branch_listing():
            raise ConnectionError("branch listing blip")
        return branch_listing

    def _downloader(self):
        def download(url, dest):
            self.downloaded.append(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")
        return download

    def _runner(self, code=0):
        def run(cmd):
            self.ran.append(cmd)
            return code
        return run

    def _read_log(self):
        with open(self.log, encoding="utf-8") as handle:
            return handle.read()

    def _pass_baseline(self, branches=(), mapping=None):
        """One clean first poll: baselines all, runs nothing."""
        return watcher.run_once(
            self.config, self._files(mapping or {}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z",
            branch_lister=self._branches(branches))

    def test_discovered_branch_prompt_is_downloaded_and_run(self):
        """A prompt on a branch found only by discovery runs once."""
        self._pass_baseline()
        report = watcher.run_once(
            self.config,
            self._files({"feature/new": ["new-prompt.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z",
            branch_lister=self._branches(["feature/new"]))
        self.assertIn("app-mode/x", self.listed)
        self.assertIn("feature/new", self.listed)
        self.assertEqual(report["launched"],
                         [("feature/new", "new-prompt.md", 0)])
        self.assertEqual(self.downloaded,
                         [os.path.join(self.downloads,
                                       "new-prompt.md")])
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})

    def test_seen_prompt_on_discovered_branch_never_runs_again(self):
        """The seen-list is global: a seen file never runs again,
        on any branch."""
        self._pass_baseline()
        watcher.save_seen(self.state, {"old-prompt.md"})
        report = watcher.run_once(
            self.config,
            self._files({"feature/new": ["old-prompt.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z",
            branch_lister=self._branches(["feature/new"]))
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.downloaded, [])
        self.assertEqual(self.ran, [])

    def test_reported_prompt_on_discovered_branch_is_skipped(self):
        """A discovered prompt with a report is marked seen and
        skipped in plain words, never downloaded or run."""
        self._pass_baseline()
        report = watcher.run_once(
            self.config,
            self._files({"feature/new": ["done-prompt.md",
                                         "done-report.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z",
            branch_lister=self._branches(["feature/new"]))
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.downloaded, [])
        self.assertEqual(self.ran, [])
        self.assertEqual(watcher.load_seen(self.state),
                         {"done-prompt.md"})
        self.assertTrue(report["skipped_reported"])
        self.assertIn("report", self._read_log().lower())

    def test_non_prompt_file_on_discovered_branch_never_runs(self):
        """Reports and other files on a discovered branch are not
        prompts: never downloaded, never run, never a failure."""
        self._pass_baseline()
        report = watcher.run_once(
            self.config,
            self._files({"feature/new": ["notes.md",
                                         "done-report.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z",
            branch_lister=self._branches(["feature/new"]))
        self.assertEqual(report["launched"], [])
        self.assertEqual(report["failed_downloads"], [])
        self.assertEqual(self.downloaded, [])
        self.assertEqual(self.ran, [])
        self.assertEqual(watcher.load_seen(self.state), set())

    def test_first_poll_baselines_every_discovered_branch(self):
        """No seen-list file: every prompt on every discovered
        branch is marked seen, nothing downloads, nothing runs,
        even on branches never watched before."""
        self.assertFalse(os.path.exists(self.state))
        report = watcher.run_once(
            self.config,
            self._files({"app-mode/x": ["old-prompt.md"],
                         "feature/new": ["new-prompt.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z",
            branch_lister=self._branches(["feature/new",
                                          "feature/other"]))
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.downloaded, [])
        self.assertEqual(self.ran, [])
        self.assertEqual(watcher.load_seen(self.state),
                         {"old-prompt.md", "new-prompt.md"})
        self.assertIn("baseline", self._read_log().lower())

    def test_branch_listing_failure_keeps_config_branches_running(self):
        """A branch-listing failure skips discovery for that poll,
        logs it plainly, and the config branches still process."""
        self._pass_baseline()
        report = watcher.run_once(
            self.config,
            self._files({"app-mode/x": ["new-prompt.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z",
            branch_lister=self._failing_branches())
        self.assertEqual(report["launched"],
                         [("app-mode/x", "new-prompt.md", 0)])
        logged = self._read_log().lower()
        self.assertIn("discover", logged)
        self.assertIn("branch", logged)

    def test_prompt_after_baseline_runs_once_then_quiet(self):
        """A prompt appearing on a discovered branch after the
        baseline runs exactly once; the next poll runs nothing."""
        self._pass_baseline(branches=["feature/new"],
                            mapping={"feature/new":
                                     ["old-prompt.md"]})
        second = watcher.run_once(
            self.config,
            self._files({"feature/new": ["old-prompt.md",
                                         "new-prompt.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z",
            branch_lister=self._branches(["feature/new"]))
        self.assertEqual(second["launched"],
                         [("feature/new", "new-prompt.md", 0)])
        third = watcher.run_once(
            self.config,
            self._files({"feature/new": ["old-prompt.md",
                                         "new-prompt.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:02:00Z",
            branch_lister=self._branches(["feature/new"]))
        self.assertEqual(third["launched"], [])
        self.assertEqual(len(self.ran), 1)

    def test_config_branch_still_runs_as_before(self):
        """No regression: a config branch prompt runs with a
        branch lister attached, discovery or none."""
        self._pass_baseline()
        report = watcher.run_once(
            self.config,
            self._files({"app-mode/x": ["new-prompt.md"]}),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z",
            branch_lister=self._branches([]))
        self.assertEqual(report["launched"],
                         [("app-mode/x", "new-prompt.md", 0)])


class TokenAuthTest(unittest.TestCase):
    """GITHUB_TOKEN auth: API calls carry it, raw downloads never do.

    The watcher lists branches and prompt files through the GitHub
    API. Without a token the API allows 60 requests per hour, so
    discovery fails on the rate limit. With GITHUB_TOKEN set, every
    api.github.com call carries Authorization: Bearer <token> and
    discovery works. The raw file download never carries it. The
    token value never reaches the log or an error message. A bad
    token (401) is logged plainly and the poll continues.
    """

    TOKEN = "test-token-abc123"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.config = {
            "branches": ["app-mode/x"],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
        }

    def _auth_of(self, request):
        """The Authorization header on a captured request, or None."""
        seen = {}
        for store in (getattr(request, "headers", {}),
                      getattr(request, "unredirected_hdrs", {})):
            for key, value in dict(store).items():
                seen[str(key).lower()] = value
        if hasattr(request, "get_header"):
            try:
                value = request.get_header("Authorization")
            except Exception:
                value = None
            if value is not None:
                return value
            try:
                value = request.get_header("AuthorizatioN")
            except Exception:
                value = None
            if value is not None:
                return value
        return seen.get("authorization")

    def _resp(self, payload, link=""):
        class _FakeResp:
            def __init__(self, payload, link):
                self._payload = payload
                self.headers = {"Link": link}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                if isinstance(self._payload, bytes):
                    return self._payload
                return json.dumps(self._payload)

        return _FakeResp(payload, link)

    def _read_log(self):
        with open(self.log, encoding="utf-8") as handle:
            return handle.read()

    def test_github_list_sends_bearer_when_token_set(self):
        """With the token set, the contents API call carries it."""
        from unittest import mock

        seen_requests = []
        payload = [{"type": "file",
                    "name": "a-prompt.md",
                    "download_url": "https://raw.invalid/a-prompt.md"}]

        def fake_urlopen(request, timeout=None):
            seen_requests.append(request)
            return self._resp(payload)

        with mock.patch.dict(os.environ,
                             {"GITHUB_TOKEN": self.TOKEN}):
            with mock.patch.object(
                    watcher.urllib.request, "urlopen",
                    side_effect=fake_urlopen):
                files = watcher.github_list(
                    "Hogathos/iris", "app-mode/x", "prompts")
        self.assertEqual(files, [{"name": "a-prompt.md",
                                  "url": "https://raw.invalid/a-prompt.md"}])
        self.assertEqual(len(seen_requests), 1)
        self.assertEqual(self._auth_of(seen_requests[0]),
                         "Bearer " + self.TOKEN)

    def test_github_list_branches_sends_bearer_on_all_pages(self):
        """With the token set, every branches page carries it,
        including paginated follow-ups."""
        from unittest import mock

        seen_requests = []
        first = self._resp(
            [{"name": "a"}],
            link='<https://api.github.com/repos/x/y/branches'
                 '?per_page=100&page=2>; rel="next"')
        second = self._resp([{"name": "b"}])

        def fake_urlopen(request, timeout=None):
            seen_requests.append(request)
            if len(seen_requests) == 1:
                return first
            return second

        with mock.patch.dict(os.environ,
                             {"GITHUB_TOKEN": self.TOKEN}):
            with mock.patch.object(
                    watcher.urllib.request, "urlopen",
                    side_effect=fake_urlopen):
                names = watcher.github_list_branches("Hogathos/iris")
        self.assertEqual(names, ["a", "b"])
        self.assertEqual(len(seen_requests), 2)
        for request in seen_requests:
            self.assertEqual(self._auth_of(request),
                             "Bearer " + self.TOKEN)

    def test_no_token_sends_no_authorization(self):
        """Without the token, neither API call carries it."""
        from unittest import mock

        seen_requests = []

        def fake_urlopen(request, timeout=None):
            seen_requests.append(request)
            url = request.get_full_url()
            if "/branches" in url:
                return self._resp([{"name": "a"}])
            return self._resp([{"type": "file",
                                "name": "a-prompt.md",
                                "download_url": "https://raw.invalid/a.md"}])

        with mock.patch.dict(os.environ):
            os.environ.pop("GITHUB_TOKEN", None)
            with mock.patch.object(
                    watcher.urllib.request, "urlopen",
                    side_effect=fake_urlopen):
                watcher.github_list(
                    "Hogathos/iris", "app-mode/x", "prompts")
                watcher.github_list_branches("Hogathos/iris")
        self.assertTrue(seen_requests)
        for request in seen_requests:
            self.assertIsNone(self._auth_of(request))

    def test_download_file_never_sends_token(self):
        """The raw file download never carries the token, even
        when the token is set."""
        from unittest import mock

        seen_requests = []

        def fake_urlopen(request, timeout=None):
            seen_requests.append(request)
            return self._resp(b"prompt body")

        dest = os.path.join(self.downloads, "a-prompt.md")
        with mock.patch.dict(os.environ,
                             {"GITHUB_TOKEN": self.TOKEN}):
            with mock.patch.object(
                    watcher.urllib.request, "urlopen",
                    side_effect=fake_urlopen):
                watcher.download_file("https://raw.invalid/a.md", dest)
        self.assertEqual(len(seen_requests), 1)
        self.assertIsNone(self._auth_of(seen_requests[0]))
        with open(dest, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "prompt body")

    def test_token_never_appears_in_log(self):
        """A normal poll with the token set never writes the token
        to the log file."""
        from unittest import mock

        watcher.save_seen(self.state, set())

        def listing(branch):
            return [{"name": "new-prompt.md",
                     "url": "https://example.invalid/new-prompt.md"}]

        def download(url, dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")

        def run(cmd):
            return 0

        with mock.patch.dict(os.environ,
                             {"GITHUB_TOKEN": self.TOKEN}):
            watcher.run_once(
                self.config, listing, download, run,
                now="2026-11-24T00:00:00Z")
        logged = self._read_log()
        self.assertNotIn(self.TOKEN, logged)

    def test_unauthorized_is_logged_plainly_and_poll_continues(self):
        """A 401 from the API is logged plainly and the poll
        continues: no crash, nothing marked seen, retry next poll."""
        import io
        import urllib.error
        from unittest import mock

        watcher.save_seen(self.state, set())
        watcher.run_once(
            self.config,
            lambda branch: [],
            lambda url, dest: None,
            lambda cmd: 0,
            now="2026-11-24T00:00:00Z",
            branch_lister=lambda: [])
        before = watcher.load_seen(self.state)

        def failing_branches():
            raise urllib.error.HTTPError(
                "https://api.github.com/repos/Hogathos/iris/branches",
                401, "Unauthorized", {}, io.BytesIO(b""))

        with mock.patch.dict(os.environ,
                             {"GITHUB_TOKEN": self.TOKEN}):
            report = watcher.run_once(
                self.config,
                lambda branch: [],
                lambda url, dest: None,
                lambda cmd: 0,
                now="2026-11-24T00:01:00Z",
                branch_lister=failing_branches)
        self.assertEqual(report["launched"], [])
        self.assertEqual(watcher.load_seen(self.state), before)
        logged = self._read_log()
        self.assertIn("401", logged)
        self.assertNotIn(self.TOKEN, logged)


class WatchedBranchesPropertyTest(unittest.TestCase):
    """The watched set is the union of config and discovery."""

    def test_union_keeps_order_without_repeats(self):
        """Config branches first, then new discovered names, each
        once: the independent check on the discovery union."""
        from hypothesis import given, strategies as st

        @given(st.lists(st.text(min_size=1), unique=True),
               st.lists(st.text(min_size=1), unique=True))
        def check(configured, discovered):
            watched = watcher.watched_branches(configured,
                                              discovered)
            self.assertEqual(set(watched),
                             set(configured) | set(discovered))
            self.assertEqual(len(watched), len(set(watched)))
            self.assertEqual(watched[:len(configured)], configured)
            rest = [name for name in discovered
                    if name not in set(configured)]
            self.assertEqual(watched[len(configured):], rest)
        check()


class PushBranchCommandTest(unittest.TestCase):
    """push_branch pushes one branch explicitly, never force.

    The watcher runs outside the sandbox as the user, so its
    git push can use the credential helper. The command names
    the branch out loud: ``git push origin <branch>``. No
    force flag under any spelling: a behind branch is logged
    and left alone.
    """

    def test_push_command_is_plain_push_origin_branch(self):
        """The exact argv, with no force flag anywhere."""
        from unittest import mock

        seen = []

        def _fake_run(cmd, **kwargs):
            seen.append((cmd, dict(kwargs)))
            return mock.Mock(returncode=0)

        with mock.patch.object(watcher.subprocess, "run",
                               side_effect=_fake_run):
            watcher.push_branch("/tmp/iris-repo", "feature/b")
        self.assertEqual(len(seen), 1)
        cmd, kwargs = seen[0]
        self.assertEqual(cmd, ["git", "-C", "/tmp/iris-repo",
                              "push", "origin", "feature/b"])
        self.assertFalse(any("force" in str(part).lower()
                             for part in cmd))
        self.assertTrue(kwargs.get("check"))

    def test_push_failure_raises_for_the_caller_to_log(self):
        """A failing push raises: run_once logs it, never retries."""
        import subprocess
        from unittest import mock

        with mock.patch.object(
                watcher.subprocess, "run",
                side_effect=subprocess.CalledProcessError(
                    1, ["git", "push"])):
            with self.assertRaises(subprocess.CalledProcessError):
                watcher.push_branch("/tmp/iris-repo", "feature/b")


class PushAfterRunTest(unittest.TestCase):
    """run_once pushes the branch after each run it starts.

    A ``muse exec`` run finishes (whatever its exit code) and
    the watcher pushes that branch to origin itself, so the
    sandbox push block stops mattering. Marked seen stays
    marked: a push failure never unmarks the run and never
    causes a re-run. A failed push never crashes the poll and
    never retries in a loop. No push happens without a run:
    not on a skip, not on a baseline.
    """

    def setUp(self):
        from unittest import mock

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "seen.json")
        self.log = os.path.join(self.tmp.name, "run.log")
        self.downloads = os.path.join(self.tmp.name, "prompts")
        self.repo_dir = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo_dir, exist_ok=True)
        self.branch = "feature/b"
        self.config = {
            "branches": [self.branch],
            "repo": "Hogathos/iris",
            "prompts_dir": "prompts",
            "poll_interval_seconds": 120,
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.downloads,
            "repo_dir": self.repo_dir,
        }
        self.ran = []
        self.pushed = []

        # These tests use an empty directory in place of a Git checkout.
        for patch in (
            mock.patch.object(watcher, "fetch_branch", return_value=None),
            mock.patch.object(watcher, "fast_forward_branch", return_value=None),
            mock.patch.object(watcher, "branch_exists_locally", return_value=True),
            mock.patch.object(watcher, "working_tree_dirty", return_value=False),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _lister(self, files):
        def listing(branch):
            self.assertEqual(branch, self.branch)
            return [{"name": name,
                     "url": "https://example.invalid/" + name}
                    for name in files]
        return listing

    def _downloader(self):
        def download(url, dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")
        return download

    def _runner(self, code=0):
        def run(cmd):
            self.ran.append(cmd)
            return code
        return run

    def _pusher(self, fail_with=None):
        def push(repo_dir, branch):
            self.pushed.append((repo_dir, branch))
            if fail_with is not None:
                raise fail_with
        return push

    def _read_log(self):
        with open(self.log, encoding="utf-8") as handle:
            return handle.read()

    def test_push_runs_after_successful_run(self):
        """Exit 0: the branch is pushed, the run is logged, and
        the log says the push went through."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value=self.branch), \
            mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(self.pushed, [(self.repo_dir, self.branch)])
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})
        logged = self._read_log()
        self.assertIn(self.branch, logged)
        self.assertIn("push", logged.lower())
        self.assertIn("pushed", logged.lower())

    def test_push_runs_after_failed_run(self):
        """A nonzero exit still marks seen and still pushes: the
        push is a separate step after the run, whatever the code."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value=self.branch), \
            mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(code=3),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 3)])
        self.assertEqual(self.pushed, [(self.repo_dir, self.branch)])
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})

    def test_no_push_on_report_skip(self):
        """A prompt skipped for its report never ran, so nothing
        pushes."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value=self.branch), \
            mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                self.config,
                self._lister(["job-prompt.md", "job-report.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.pushed, [])
        self.assertEqual(self.ran, [])

    def test_no_push_on_dirty_tree_skip(self):
        """Uncommitted changes skip the run, so nothing pushes and
        the prompt stays unmarked."""
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=True), \
            mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.pushed, [])
        self.assertEqual(watcher.load_seen(self.state), set())

    def test_no_push_on_fetch_failure_skip(self):
        """A failing fetch skips the run, so nothing pushes."""
        import subprocess
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value="other-branch"), \
            mock.patch.object(
                watcher, "working_tree_dirty",
                return_value=False), \
            mock.patch.object(
                watcher, "branch_exists_locally",
                return_value=False), \
            mock.patch.object(
                watcher, "fetch_branch",
                side_effect=subprocess.CalledProcessError(
                    1, ["git", "fetch"])), \
            mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.pushed, [])
        self.assertEqual(watcher.load_seen(self.state), set())

    def test_no_push_on_baseline(self):
        """The first poll runs nothing, so nothing pushes."""
        from unittest import mock

        self.assertFalse(os.path.exists(self.state))
        with mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                self.config, self._lister(["old-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.pushed, [])
        self.assertEqual(self.ran, [])

    def test_push_failure_is_logged_and_poll_continues(self):
        """A failing push never crashes the poll, never unmarks
        the run, and never retries: the next poll is quiet."""
        import subprocess
        from unittest import mock

        watcher.save_seen(self.state, set())
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value=self.branch), \
            mock.patch.object(
                watcher, "push_branch",
                side_effect=self._pusher(
                    fail_with=subprocess.CalledProcessError(
                        1, ["git", "push"]))):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(len(self.pushed), 1)
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})
        logged = self._read_log().lower()
        self.assertIn("push", logged)
        self.assertIn("fail", logged)
        self.assertIn(self.branch.lower(), logged)
        second = watcher.run_once(
            self.config, self._lister(["new-prompt.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z")
        self.assertEqual(second["launched"], [])
        self.assertEqual(len(self.pushed), 1)

    def test_behind_branch_is_logged_not_rebased(self):
        """A non-fast-forward push failure is logged in plain
        words and the poll moves on: no rebase, no retry, the
        run stays marked seen."""
        import subprocess
        from unittest import mock

        watcher.save_seen(self.state, set())
        failure = subprocess.CalledProcessError(
            1, ["git", "-C", self.repo_dir,
                "push", "origin", self.branch],
            output="",
            stderr="rejected: non-fast-forward")
        with mock.patch.object(
                watcher, "get_current_branch",
                return_value=self.branch), \
            mock.patch.object(
                watcher, "checkout_branch") as checkout, \
            mock.patch.object(
                subprocess, "run", wraps=subprocess.run) as git_run, \
            mock.patch.object(
                watcher, "push_branch",
                side_effect=self._pusher(fail_with=failure)):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
            second = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:01:00Z")
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(watcher.load_seen(self.state),
                         {"new-prompt.md"})
        checkout.assert_not_called()
        self.assertFalse(any("rebase" in call.args[0]
                             for call in git_run.call_args_list))
        self.assertEqual(second["launched"], [])
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(self.pushed, [(self.repo_dir, self.branch)])
        self.assertIn(f"push branch={self.branch} push failed", self._read_log())

    def test_discovered_branch_pushes_too(self):
        """Auto-discovered branches push exactly like config
        branches: the push step sits on the shared run path."""
        from unittest import mock

        watcher.run_once(
            self.config, self._lister([]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z",
            branch_lister=lambda: [])

        def listing(branch):
            return [{"name": "new-prompt.md",
                     "url": "https://example.invalid/new-prompt.md"}]

        with mock.patch.object(
                watcher, "get_current_branch",
                return_value=self.branch), \
            mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                dict(self.config, branches=["other/config-branch"]),
                listing, self._downloader(), self._runner(),
                now="2026-11-24T00:01:00Z",
                branch_lister=lambda: [self.branch])
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(self.pushed, [(self.repo_dir, self.branch)])

    def test_no_push_without_repo_dir(self):
        """With no repo_dir there is no tree to push from, so a
        run completes with no push attempt and no crash."""
        from unittest import mock

        config = dict(self.config)
        del config["repo_dir"]
        watcher.save_seen(self.state, set())
        with mock.patch.object(watcher, "push_branch",
                              side_effect=self._pusher()):
            report = watcher.run_once(
                config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(self.pushed, [])


class CodexPromptNamesTest(unittest.TestCase):
    """Both agents share the prompt directory."""

    def test_prompt_names_for_both_agents(self):
        for name in ("foo-codex-prompt.md", "foo-prompt.md"):
            with self.subTest(name=name):
                self.assertTrue(watcher.is_prompt_file(name))
        for name in ("foo-codex-report.md", "foo-report.md", "note.md", ".md"):
            with self.subTest(name=name):
                self.assertFalse(watcher.is_prompt_file(name))

    def test_report_names_for_both_agents(self):
        self.assertEqual(watcher.report_name("foo-codex-prompt.md"), "foo-codex-report.md")
        self.assertEqual(watcher.report_name("foo-prompt.md"), "foo-report.md")


class CodexCommandTest(unittest.TestCase):
    """Build the verified Windows launch and keep its output visible."""

    def test_build_codex_command_reads_a_literal_prompt_path(self):
        path = r"C:\Iris\Charles's prompt & file.md"
        self.assertEqual(
            watcher.build_codex_command(path),
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                r"Get-Content -LiteralPath 'C:\Iris\Charles''s prompt & file.md' "
                "-Raw -Encoding UTF8 -ErrorAction Stop | "
                "& codex -a never exec --sandbox workspace-write --ephemeral "
                "-m gpt-6.2-sol -; exit $LASTEXITCODE",
            ],
        )

    def _run_with_mock_subprocess(self, repo_dir):
        from unittest import mock

        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "exit 7"]
        with mock.patch.object(watcher.subprocess, "run", return_value=mock.Mock(returncode=7)) as run:
            code = watcher.run_codex(command, repo_dir)
        run.assert_called_once()
        self.assertEqual(run.call_args.args, (command,))
        self.assertEqual(code, 7)
        return run.call_args.kwargs

    def test_run_codex_sets_subprocess_cwd(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            kwargs = self._run_with_mock_subprocess(repo_dir)
            self.assertEqual(kwargs.get("cwd"), repo_dir)

    def test_run_codex_inherits_terminal_output(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            kwargs = self._run_with_mock_subprocess(repo_dir)
        self.assertFalse(kwargs.get("capture_output", False))
        self.assertIsNone(kwargs.get("stdout"))
        self.assertIsNone(kwargs.get("stderr"))


class CodexRunOnceTest(unittest.TestCase):
    """Route each prompt once and record the result on disk."""

    branch = "bridge/codex-test"
    now = "2026-11-26T12:00:00Z"
    codex_prompt = "foo-codex-prompt.md"
    muse_prompt = "foo-prompt.md"

    def setUp(self):
        from unittest import mock

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = os.path.join(temp.name, "seen.json")
        self.log = os.path.join(temp.name, "launch.log")
        self.download_dir = os.path.join(temp.name, "prompts")
        self.repo_dir = os.path.join(temp.name, "repo")
        os.makedirs(self.repo_dir)
        self.config = {
            "branches": [self.branch],
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": self.download_dir,
            "repo_dir": self.repo_dir,
        }
        watcher.save_seen(self.state, set())
        current_branch = mock.patch.object(watcher, "get_current_branch", return_value=self.branch)
        push_branch = mock.patch.object(watcher, "push_branch")
        for patch in (
            current_branch,
            push_branch,
            mock.patch.object(watcher, "fetch_branch", return_value=None),
            mock.patch.object(watcher, "fast_forward_branch", return_value=None),
            mock.patch.object(watcher, "branch_exists_locally", return_value=True),
            mock.patch.object(watcher, "working_tree_dirty", return_value=False),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.muse_runner = mock.Mock(return_value=0)
        self.codex_calls = []
        self.codex_exit_code = 0

    def _poll(self, names):
        def list_prompts(branch):
            self.assertEqual(branch, self.branch)
            return [{"name": name, "url": "https://example.invalid/" + name} for name in names]

        def download(url, destination):
            _write(destination, "Prompt from " + url)

        def run_codex(command, cwd):
            self.codex_calls.append((command, cwd))
            return self.codex_exit_code

        return watcher.run_once(
            dict(self.config),
            list_prompts,
            download,
            self.muse_runner,
            now=self.now,
            codex_runner=run_codex,
        )

    def test_run_once_routes_to_the_matching_builder_and_runner(self):
        from unittest import mock

        codex_command = ["codex test command"]
        muse_command = ["muse test command"]
        with mock.patch.object(watcher, "build_codex_command", return_value=codex_command) as codex_builder:
            with mock.patch.object(watcher, "build_command", return_value=muse_command) as muse_builder:
                self._poll([self.codex_prompt])
                codex_builder.assert_called_once_with(os.path.join(self.download_dir, self.codex_prompt))
                muse_builder.assert_not_called()
                self.muse_runner.assert_not_called()
                self.assertEqual(self.codex_calls, [(codex_command, self.repo_dir)])

                self._poll([self.muse_prompt])
                muse_builder.assert_called_once_with(os.path.join(self.download_dir, self.muse_prompt))
                self.muse_runner.assert_called_once_with(muse_command)
                self.assertEqual(codex_builder.call_count, 1)
                self.assertEqual(self.codex_calls, [(codex_command, self.repo_dir)])

    def test_launch_log_names_each_agent_and_its_result(self):
        self.codex_exit_code = 7
        self._poll([self.codex_prompt, self.muse_prompt])
        with open(self.log, encoding="utf-8") as handle:
            launches = [line.split() for line in handle if " exit=" in line]
        self.assertCountEqual(
            launches,
            [
                [self.now, f"branch={self.branch}", f"file={self.codex_prompt}", "agent=codex", "exit=7"],
                [self.now, f"branch={self.branch}", f"file={self.muse_prompt}", "agent=muse", "exit=0"],
            ],
        )

    def test_seen_codex_prompt_is_skipped_after_restart(self):
        watcher.save_seen(self.state, {self.codex_prompt})
        self.assertEqual(self._poll([self.codex_prompt])["launched"], [])
        self.assertEqual(self.codex_calls, [])
        self.muse_runner.assert_not_called()

    def test_failed_codex_launch_is_saved_and_skipped_after_restart(self):
        self.codex_exit_code = 7
        first = self._poll([self.codex_prompt])
        self.assertEqual(first["launched"], [(self.branch, self.codex_prompt, 7)])
        self.assertEqual(watcher.load_seen(self.state), {self.codex_prompt})
        self.assertEqual(self._poll([self.codex_prompt])["launched"], [])
        self.assertEqual(len(self.codex_calls), 1)
        self.muse_runner.assert_not_called()


class FetchBeforeCheckoutTest(unittest.TestCase):
    """Exercise the watcher against real local and remote Git refs."""

    branch = "bridge/test-fetch"
    prompt = "new-codex-prompt.md"
    other_prompt = "next-prompt.md"

    def setUp(self):
        from unittest import mock

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.origin = os.path.join(tmp.name, "origin")
        self.repo = os.path.join(tmp.name, "repo")
        self.state = os.path.join(tmp.name, "seen.json")
        self.log = os.path.join(tmp.name, "run.log")
        self.config = {
            "branches": [self.branch],
            "state_file": self.state,
            "log_file": self.log,
            "download_dir": os.path.join(tmp.name, "downloads"),
            "repo_dir": self.repo,
        }

        os.mkdir(self.origin)
        self._git(self.origin, "init", "--initial-branch=main")
        self._commit(self.origin, "work.txt", "base")
        self._git(self.origin, "branch", self.branch)
        self._git(self.origin, "clone", "--quiet", self.origin, self.repo)
        self._git(self.repo, "branch", self.branch, f"origin/{self.branch}")
        self.stale_head = self._git(self.repo, "rev-parse", self.branch)

        self._git(self.origin, "checkout", self.branch)
        prompt_path = f"prompts/{self.prompt}"
        _write(os.path.join(self.origin, prompt_path), "new prompt")
        self._git(self.origin, "add", "--", prompt_path)
        self.remote_head = self._commit(
            self.origin, "work.txt", "remote prompt commit")
        self.assertNotEqual(self.stale_head, self.remote_head)
        self.assertEqual(
            self._git(self.repo, "rev-parse", f"origin/{self.branch}"),
            self.stale_head,
        )

        self.muse = mock.Mock(return_value=0)
        self.codex = mock.Mock(return_value=0)
        push = mock.patch.object(watcher, "push_branch")
        self.push = push.start()
        self.addCleanup(push.stop)

    def _git(self, repo, *args):
        import subprocess

        return subprocess.run(
            ["git", "-C", repo, *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def _commit(self, repo, path, text):
        _write(os.path.join(repo, path), text)
        self._git(repo, "add", "--", path)
        self._git(
            repo, "-c", "user.name=Watcher Test",
            "-c", "user.email=watcher@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", text,
        )
        return self._git(repo, "rev-parse", "HEAD")

    def _poll(self, *, include_main=False):
        files = {self.branch: self.prompt}
        if include_main:
            files["main"] = self.other_prompt

        def list_prompts(branch):
            return [{"name": files[branch],
                     "url": "https://example.invalid/" + files[branch]}]

        return watcher.run_once(
            dict(self.config, branches=list(files)),
            list_prompts,
            lambda url, destination: _write(destination, url),
            self.muse,
            now="2026-11-26T12:00:00Z",
            codex_runner=self.codex,
        )

    def _assert_skip(self, result, reason):
        self.assertEqual(len(result["skipped_checkouts"]), 1)
        branch, name, message = result["skipped_checkouts"][0]
        self.assertEqual((branch, name), (self.branch, self.prompt))
        self.assertRegex(message.lower(), reason)
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn(
                f"skip branch={branch} file={name} reason={message}",
                handle.read(),
            )

    def _assert_no_run(self, result):
        self.assertEqual(result["launched"], [])
        self.muse.assert_not_called()
        self.codex.assert_not_called()
        self.push.assert_not_called()

    def test_existing_branch_is_updated_before_checkout_and_run(self):
        from unittest import mock

        watcher.save_seen(self.state, set())
        checkout_branch = watcher.checkout_branch
        heads_before_checkout = []

        def observe_checkout(repo, branch):
            heads_before_checkout.append((
                self._git(repo, "rev-parse", branch),
                self._git(repo, "rev-parse", f"origin/{branch}"),
            ))
            checkout_branch(repo, branch)

        def run_codex(command, repo):
            self.assertEqual(self._git(repo, "branch", "--show-current"), self.branch)
            self._commit(repo, "result.txt", "agent result")
            return 0

        self.codex.side_effect = run_codex
        with mock.patch.object(watcher, "checkout_branch", side_effect=observe_checkout):
            result = self._poll()

        self.assertEqual(
            heads_before_checkout, [(self.remote_head, self.remote_head)],
            "Both branch refs must include the remote commit before checkout.",
        )
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD^"), self.remote_head)
        self.assertEqual(result["launched"], [(self.branch, self.prompt, 0)])
        self.assertEqual(watcher.load_seen(self.state), {self.prompt})
        self.muse.assert_not_called()
        self.push.assert_called_once_with(self.repo, self.branch)

    def test_fetch_failure_skips_existing_branch_and_poll_continues(self):
        watcher.save_seen(self.state, set())
        self._git(self.origin, "checkout", "main")
        self._git(self.origin, "branch", "-D", self.branch)

        result = self._poll(include_main=True)

        self.codex.assert_not_called()
        self.assertEqual(result["launched"], [("main", self.other_prompt, 0)])
        self.muse.assert_called_once()
        self.push.assert_called_once_with(self.repo, "main")
        self.assertEqual(watcher.load_seen(self.state), {self.other_prompt})
        self.assertEqual(self._git(self.repo, "rev-parse", self.branch), self.stale_head)
        self._assert_skip(result, r"fetch.*fail")

    def test_diverged_branch_preserves_local_work_without_checkout(self):
        from unittest import mock

        watcher.save_seen(self.state, set())
        self._git(self.repo, "checkout", self.branch)
        local_head = self._commit(self.repo, "work.txt", "local work")
        self._git(self.repo, "checkout", "main")
        with mock.patch.object(
            watcher, "checkout_branch", wraps=watcher.checkout_branch,
        ) as checkout:
            result = self._poll()

        checkout.assert_not_called()
        self._assert_no_run(result)
        self.assertEqual(watcher.load_seen(self.state), set())
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(self._git(self.repo, "rev-parse", self.branch), local_head)
        self.assertEqual(self._git(self.repo, "show", f"{self.branch}:work.txt"), "local work")
        self.assertEqual(self._git(self.repo, "rev-parse", f"origin/{self.branch}"), self.remote_head)
        self._assert_skip(result, r"diverg|fast.forward")

    def test_already_on_branch_starts_run_at_fetched_head(self):
        from unittest import mock

        watcher.save_seen(self.state, set())
        self._git(self.repo, "checkout", self.branch)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), self.stale_head)
        run_heads = []

        def run_codex(command, repo):
            run_heads.append(self._git(repo, "rev-parse", "HEAD"))
            self._commit(repo, "result.txt", "agent result")
            return 0

        self.codex.side_effect = run_codex
        with mock.patch.object(
            watcher, "checkout_branch", wraps=watcher.checkout_branch,
        ) as checkout:
            result = self._poll()

        self.assertEqual(run_heads, [self.remote_head])
        checkout.assert_not_called()
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), self.branch)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD^"), self.remote_head)
        self.assertEqual(
            self._git(self.repo, "rev-parse", f"origin/{self.branch}"), self.remote_head,
        )
        self.assertEqual(result["launched"], [(self.branch, self.prompt, 0)])
        self.assertEqual(watcher.load_seen(self.state), {self.prompt})
        self.push.assert_called_once_with(self.repo, self.branch)

    def test_already_on_branch_fetch_failure_skips_and_poll_continues(self):
        watcher.save_seen(self.state, set())
        self._git(self.repo, "checkout", self.branch)
        self._git(self.origin, "checkout", "main")
        self._git(self.origin, "branch", "-D", self.branch)

        result = self._poll(include_main=True)

        self.codex.assert_not_called()
        self.assertEqual(result["launched"], [("main", self.other_prompt, 0)])
        self.muse.assert_called_once()
        self.push.assert_called_once_with(self.repo, "main")
        self.assertEqual(watcher.load_seen(self.state), {self.other_prompt})
        self.assertEqual(self._git(self.repo, "rev-parse", self.branch), self.stale_head)
        self._assert_skip(result, r"fetch.*fail")

    def test_already_on_diverged_branch_skips_and_preserves_work(self):
        from unittest import mock

        watcher.save_seen(self.state, set())
        self._git(self.repo, "checkout", self.branch)
        local_head = self._commit(self.repo, "work.txt", "local work")
        with mock.patch.object(
            watcher, "checkout_branch", wraps=watcher.checkout_branch,
        ) as checkout:
            result = self._poll()

        self._assert_no_run(result)
        checkout.assert_not_called()
        self.assertEqual(watcher.load_seen(self.state), set())
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), self.branch)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), local_head)
        self.assertEqual(self._git(self.repo, "status", "--porcelain"), "")
        with open(os.path.join(self.repo, "work.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "local work")
        self.assertEqual(
            self._git(self.repo, "rev-parse", f"origin/{self.branch}"), self.remote_head,
        )
        self._assert_skip(result, r"diverg|fast.forward")

    def test_dirty_tree_keeps_uncommitted_work(self):
        watcher.save_seen(self.state, set())
        dirty_path = os.path.join(self.repo, "work.txt")
        _write(dirty_path, "uncommitted work")

        result = self._poll()

        self._assert_no_run(result)
        self.assertEqual(watcher.load_seen(self.state), set())
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(self._git(self.repo, "rev-parse", self.branch), self.stale_head)
        with open(dirty_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "uncommitted work")
        self._assert_skip(result, r"uncommitted")

    def test_missing_branch_stays_missing_after_failed_fetch(self):
        watcher.save_seen(self.state, set())
        self._git(self.repo, "branch", "-D", self.branch)
        self._git(self.repo, "branch", "-dr", f"origin/{self.branch}")
        self._git(self.origin, "checkout", "main")
        self._git(self.origin, "branch", "-D", self.branch)

        result = self._poll()

        self._assert_no_run(result)
        self.assertEqual(watcher.load_seen(self.state), set())
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(self._git(self.repo, "branch", "--list", self.branch), "")
        self._assert_skip(result, r"fetch.*fail")

    def test_baseline_marks_prompt_seen_and_pushes_nothing(self):
        result = self._poll()

        self._assert_no_run(result)
        self.assertEqual(result["baselined"], [self.prompt])
        self.assertEqual(watcher.load_seen(self.state), {self.prompt})
        self.assertEqual(self._git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(self._git(self.repo, "rev-parse", self.branch), self.stale_head)


def test_run_status_uses_a_two_minute_timer():
    assert watcher.STATUS_INTERVAL_SECONDS == 120


def _run_status_round_trip(tmp_path, capfd, agent, exit_code):
    """Read the live and final snapshots back from a bare Git origin."""
    import re
    import subprocess
    from datetime import datetime, timedelta
    from textwrap import dedent
    from unittest import mock

    branch = "bridge/status-test"
    stem = "progress-codex" if agent == "codex" else "progress"
    prompt_name = f"{stem}-prompt.md"
    report_path = f"prompts/{stem}-report.md"
    status_path = "prompts/progress-status.md"
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    state = tmp_path / "seen.json"
    log = tmp_path / "launch.log"
    config = tmp_path / "watcher.json"
    script = tmp_path / "agent.py"

    def git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()

    git(tmp_path, "init", "--bare", str(origin))
    git(tmp_path, "init", "--initial-branch=" + branch, str(repo))
    git(repo, "config", "user.name", "Watcher Test")
    git(repo, "config", "user.email", "watcher@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "remote", "add", "origin", str(origin))
    _write(repo / "prompts" / prompt_name, "Test prompt")
    git(repo, "add", "--", "prompts/" + prompt_name)
    git(repo, "commit", "-m", "Add test prompt")
    git(repo, "push", "origin", branch)
    watcher.save_seen(state, set())
    config.write_text(
        json.dumps(
            {
                "branches": [branch],
                "repo_dir": str(repo),
                "state_file": str(state),
                "log_file": str(log),
                "download_dir": str(tmp_path / "downloads"),
            }
        ),
        encoding="utf-8",
    )
    script.write_text(
        dedent(
            """\
            import subprocess
            import sys
            import time
            from pathlib import Path

            origin, branch, status, report, initial, exit_code = sys.argv[1:]
            status_path = Path(status)
            if not status_path.is_file():
                sys.exit("The local status file was missing when the agent started.")
            Path(initial).write_text(status_path.read_text(encoding="utf-8"), encoding="utf-8")
            Path("unrelated.txt").write_text("staged by agent", encoding="utf-8")
            subprocess.run(["git", "add", "--", "unrelated.txt"], check=True)
            for line in range(1, 76):
                print(f"output line {line:03d}", flush=True)
            print("last stderr line", file=sys.stderr, flush=True)

            live = set()
            deadline = time.monotonic() + 15
            while len(live) < 2 and time.monotonic() < deadline:
                git = ["git", "-C", origin]
                head = subprocess.check_output(git + ["rev-parse", branch], text=True).strip()
                snapshot = subprocess.run(git + ["show", f"{head}:{status}"], capture_output=True, text=True)
                if "last stderr line" in snapshot.stdout and "State: running" in snapshot.stdout:
                    live.add(head)
                time.sleep(0.1)
            if len(live) < 2:
                sys.exit("Two timed snapshots did not reach the origin while the agent was silent.")

            print("last line before exit", flush=True)
            Path(report).write_text("Agent report\\n", encoding="utf-8")
            subprocess.run(["git", "add", "--", report], check=True)
            subprocess.run(["git", "commit", "--only", "-m", "Add agent report", "--", report], check=True)
            sys.exit(int(exit_code))
            """
        ),
        encoding="utf-8",
    )
    initial = tmp_path / "initial-status.md"
    command = [sys.executable, "-u", str(script), str(origin), branch, status_path, report_path, str(initial), str(exit_code)]
    listing = [{"name": prompt_name, "url": "https://example.invalid/" + prompt_name}]
    builder = "build_codex_command" if agent == "codex" else "build_command"
    other_builder = "build_command" if agent == "codex" else "build_codex_command"
    with (
        mock.patch.object(watcher, "github_list", return_value=listing),
        mock.patch.object(watcher, "github_list_branches", return_value=[branch]),
        mock.patch.object(watcher, "download_file", side_effect=lambda url, dest: _write(dest, "Test prompt")),
        mock.patch.object(watcher, "STATUS_INTERVAL_SECONDS", 0.75, create=True),
        mock.patch.object(watcher, builder, return_value=command) as build,
        mock.patch.object(watcher, other_builder) as other,
    ):
        assert watcher.main(["--config", str(config), "--once"]) == 0  # Baseline branch discovery.
        assert watcher.main(["--config", str(config), "--once"]) == 0

    terminal = capfd.readouterr()
    assert initial.is_file(), terminal.out + terminal.err
    build.assert_called_once_with(str(tmp_path / "downloads" / prompt_name))
    other.assert_not_called()
    assert "output line 001" in terminal.out
    assert "last stderr line" in terminal.out + terminal.err
    assert "last line before exit" in terminal.out
    assert watcher.load_seen(state) == {prompt_name}
    assert f"agent={agent} exit={exit_code}" in log.read_text(encoding="utf-8")

    def timestamp(content, label):
        match = re.search(rf"^{label}: (\S+)$", content, re.MULTILINE)
        assert match, content
        result = datetime.fromisoformat(match[1].replace("Z", "+00:00"))
        assert result.utcoffset() == timedelta(0)
        return result

    first = initial.read_text(encoding="utf-8")
    assert f"Agent: {agent}" in first
    assert f"Prompt: {prompt_name}" in first
    assert "State: running" in first
    started = timestamp(first, "Started")
    assert started <= timestamp(first, "Last updated")

    commits = git(origin, "log", "--format=%H", "--reverse", branch, "--", status_path).splitlines()
    live = [git(origin, "show", f"{commit}:{status_path}") for commit in commits[:-1]]
    assert len(live) >= 2
    for snapshot in live[-2:]:
        assert "State: running" in snapshot
        assert timestamp(snapshot, "Started") == started
        assert "output line 001" not in snapshot
        assert "output line 075" in snapshot
        assert "last stderr line" in snapshot
        assert 1 <= len(re.findall(r"output line \d{3}", snapshot)) <= 50
    assert timestamp(live[-2], "Last updated") < timestamp(live[-1], "Last updated")
    assert git(origin, "ls-tree", commits[-2], "--", report_path) == ""

    final = git(origin, "show", f"{branch}:{status_path}")
    state_name = "done" if exit_code == 0 else "failed"
    assert f"State: {state_name}" in final
    assert timestamp(final, "Started") == started
    assert timestamp(final, "Last updated") >= timestamp(live[-1], "Last updated")
    assert "last line before exit" in final
    assert (repo / status_path).read_text(encoding="utf-8").strip() == final
    assert git(origin, "show", f"{branch}:{report_path}") == "Agent report"
    assert git(origin, "ls-tree", branch, "--", "unrelated.txt") == ""
    assert git(repo, "diff", "--cached", "--name-only") == "unrelated.txt"


def test_run_status_muse_publishes_live_output_and_finishes_done(tmp_path, capfd):
    _run_status_round_trip(tmp_path, capfd, "muse", 0)


def test_run_status_codex_publishes_live_output_and_finishes_failed(tmp_path, capfd):
    _run_status_round_trip(tmp_path, capfd, "codex", 7)


if __name__ == "__main__":
    unittest.main()
