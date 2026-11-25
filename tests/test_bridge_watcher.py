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
    missing after a fetch is skipped as before. A branch that
    already exists locally is never fetched.
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

    def test_existing_local_branch_is_never_fetched(self):
        """A branch that already exists locally runs as normal.
        The watcher makes no extra git fetch call on that path."""
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
                return_value=True), \
            mock.patch.object(
                watcher, "fetch_branch") as fetch, \
            mock.patch.object(
                watcher, "checkout_branch",
                side_effect=lambda d, b: self.order.append("checkout")):
            report = watcher.run_once(
                self.config, self._lister(["new-prompt.md"]),
                self._downloader(), self._runner(),
                now="2026-11-24T00:00:00Z")
        fetch.assert_not_called()
        self.assertEqual(self.order, ["checkout", "run"])
        self.assertEqual(report["launched"],
                         [(self.branch, "new-prompt.md", 0)])
        self.assertEqual(watcher.load_seen(self.state), {"new-prompt.md"})

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


if __name__ == "__main__":
    unittest.main()
