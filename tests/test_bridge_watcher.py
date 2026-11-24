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

    def test_missing_file_gives_defaults(self):
        """A missing config means first run: every default holds."""
        config = watcher.load_config(self.missing)
        self.assertEqual(config["branches"], ["app-mode/packaging"])
        self.assertEqual(config["repo"], "Hogathos/iris")
        self.assertEqual(config["prompts_dir"], "prompts")
        self.assertEqual(config["poll_interval_seconds"], 120)
        self.assertTrue(config["state_file"].endswith(
            "bridge_watcher_seen.json"))
        self.assertTrue(config["log_file"].endswith(
            "bridge_watcher.log"))
        self.assertTrue(config["download_dir"].endswith(
            "bridge_prompts"))

    def test_missing_keys_fall_back_to_defaults(self):
        """A partial config fills the gaps with defaults."""
        config = watcher.load_config(
            self._config_path({"branches": ["app-mode/x"]}))
        self.assertEqual(config["branches"], ["app-mode/x"])
        self.assertEqual(config["poll_interval_seconds"], 120)
        self.assertEqual(config["repo"], "Hogathos/iris")

    def test_no_config_at_all_gives_defaults(self):
        """No path means defaults rooted next to the module."""
        config = watcher.load_config(None)
        self.assertEqual(config["branches"], ["app-mode/packaging"])
        self.assertTrue(os.path.dirname(
            config["state_file"]) == os.path.dirname(
            watcher.__file__))

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
             "--disable-approval"])


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
        watcher.save_seen(self.state, {"old.md"})
        report = watcher.run_once(
            self.config, self._lister(["old.md", "new.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(
            self.ran[0],
            ["muse", "exec", "--prompt-file",
             os.path.join(self.downloads, "new.md"),
             "--disable-approval"])
        with open(self.log, encoding="utf-8") as handle:
            logged = handle.read()
        self.assertIn("2026-11-24T00:00:00Z", logged)
        self.assertIn("app-mode/x", logged)
        self.assertIn("new.md", logged)
        self.assertIn("0", logged)
        self.assertEqual(report["launched"],
                         [("app-mode/x", "new.md", 0)])
        self.assertEqual(watcher.load_seen(self.state),
                         {"old.md", "new.md"})

    def test_second_poll_runs_nothing(self):
        """The seen-list survives: a rerun finds no work."""
        watcher.save_seen(self.state, set())
        watcher.run_once(
            self.config, self._lister(["new.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:00:00Z")
        second = watcher.run_once(
            self.config, self._lister(["new.md"]),
            self._downloader(), self._runner(),
            now="2026-11-24T00:01:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(second["launched"], [])

    def test_failed_run_is_still_marked_seen(self):
        """A failed Muse run is logged with its code and never
        runs again. Iris never double-runs a prompt."""
        watcher.save_seen(self.state, set())
        report = watcher.run_once(
            self.config, self._lister(["new.md"]),
            self._downloader(), self._runner(code=3),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(report["launched"],
                         [("app-mode/x", "new.md", 3)])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn("3", handle.read().splitlines()[-1])
        self.assertEqual(watcher.load_seen(self.state), {"new.md"})

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
            if url.endswith("bad.md"):
                raise ConnectionError("blip")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write("prompt")
        report = watcher.run_once(
            self.config, self._lister(["bad.md", "good.md"]),
            download, self._runner(),
            now="2026-11-24T00:00:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertIn("good.md", self.ran[0][3])
        self.assertEqual(watcher.load_seen(self.state), {"good.md"})
        self.assertEqual(report["failed_downloads"], ["bad.md"])


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
            code = watcher.run_muse(cmd)
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
                code = watcher.run_muse(cmd)
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
            self.config, self._lister(["a.md", "b.md"]),
            self._downloader(),
            lambda cmd: self.ran.append(cmd) or 0,
            now="2026-11-24T00:00:00Z")
        self.assertEqual(self.ran, [])
        self.assertEqual(self.downloaded, [])
        self.assertEqual(watcher.load_seen(self.state),
                         {"a.md", "b.md"})
        self.assertEqual(first["launched"], [])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn("baseline", handle.read().lower())
        second = watcher.run_once(
            self.config, self._lister(["a.md", "b.md"]),
            self._downloader(),
            lambda cmd: self.ran.append(cmd) or 0,
            now="2026-11-24T00:01:00Z")
        self.assertEqual(self.ran, [])
        self.assertEqual(second["launched"], [])
        third = watcher.run_once(
            self.config, self._lister(["a.md", "b.md", "c.md"]),
            self._downloader(),
            lambda cmd: self.ran.append(cmd) or 0,
            now="2026-11-24T00:02:00Z")
        self.assertEqual(len(self.ran), 1)
        self.assertIn("c.md", self.ran[0][3])
        self.assertEqual(third["launched"],
                         [("app-mode/x", "c.md", 0)])


if __name__ == "__main__":
    unittest.main()
