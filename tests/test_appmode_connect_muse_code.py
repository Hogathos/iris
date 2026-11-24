"""Connect Muse Code tests: Iris writes its proxy setting.

Iris connects Muse Code to the Iris proxy with one click, the
same as the other agents. Muse Code is Meta's `muse` coding
agent CLI. Its settings live at `~/.config/muse/settings.json`
and require `"schema_version": 1`. Its proxy setting is
`endpoint_transport.proxy` inside that file.

These tests are the contract for the Muse Code half of
appmode/connect.py and the Muse Code branch of
appmode/restart.py, which do not exist yet. They fail until
the modules are implemented. The test writer is not the
implementer.

Rules under test (from
specs/app-mode-connect-muse-code/requirements.md):
- Iris backs up the settings file before it writes.
- Iris writes endpoint_transport.proxy, then reads the file back.
- Iris preserves every other key: schema_version, model,
  mcp_servers, and anything else the user set.
- Iris shows the user the change in plain words.
- If Muse Code already has a proxy setting, Iris refuses in
  plain words and changes nothing.
- If the settings file is missing, Iris refuses in plain words.
- If the file is not a dict, or its schema version is missing
  or not 1, Iris refuses in plain words and changes nothing.
- If a backup already waits, Iris refuses instead of
  overwriting it.
- If the file changes while Iris is connecting, Iris refuses
  instead of writing over the agent's change with stale data.
- Iris is loopback only. A proxy URL that points elsewhere, or
  that is not a URL at all, is refused, and nothing is written.
- One click disconnects. Iris restores the backup byte for byte.
- The result never carries a secret. The settings file can hold
  mcp_servers entries with headers and tokens. The result names
  the proxy only, never the whole config.
- Restart returns the Muse Code manual instruction, not the
  Cursor wording. Muse Code is a terminal CLI, so Iris cannot
  restart it the way it restarts Cursor.

Tests use a temp home dir, never the real one. Config paths
come from the module, given a home dir, so the same test runs
on all three OSes in CI.
"""
import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from appmode import connect as connect_module
from appmode import restart as restart_module

# The proxy Iris writes: its own loopback address.
PROXY_URL = "http://127.0.0.1:8888"

# A proxy the user already had, so the refuse path is exercised.
EXISTING_PROXY_URL = "http://proxy.example.com:8080"


def _muse_settings(proxy=None, schema_version=1):
    """A Muse Code settings.json dict, with or without a proxy."""
    settings = {
        "schema_version": schema_version,
        "model": "muse-spark-1.3-contributor",
        "mcp_servers": {
            "example": {
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
                "headers": {
                    "Authorization": "Bearer sk-test-only",
                },
            },
        },
    }
    if proxy is not None:
        settings["endpoint_transport"] = {"proxy": proxy}
    return settings


class MuseCodeConnectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, content):
        """Write a Muse Code settings file, return its path."""
        path = connect_module.config_path("muse-code", self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_muse_code_connect_writes_proxy_and_backs_up(self):
        path = self._write(json.dumps(_muse_settings()))
        original = self._read(path)
        result = connect_module.connect_agent(
            "muse-code", PROXY_URL, self.home)
        # The settings now carry the Iris proxy.
        settings = json.loads(self._read(path))
        self.assertEqual(
            settings["endpoint_transport"]["proxy"], PROXY_URL)
        # The backup holds the original content, byte for byte.
        self.assertEqual(
            self._read(path + ".iris-backup"), original)
        # The result is plain words: agent, file, change.
        self.assertIn("Muse Code", result)
        self.assertIn(path, result)
        self.assertIn(PROXY_URL, result)

    def test_muse_code_connect_preserves_other_keys(self):
        path = self._write(json.dumps(_muse_settings()))
        connect_module.connect_agent(
            "muse-code", PROXY_URL, self.home)
        settings = json.loads(self._read(path))
        self.assertEqual(settings["schema_version"], 1)
        self.assertEqual(
            settings["model"], "muse-spark-1.3-contributor")
        self.assertIn("example", settings["mcp_servers"])
        self.assertEqual(
            settings["mcp_servers"]["example"]["url"],
            "https://example.com/mcp")

    def test_muse_code_connect_refuses_when_proxy_already_set(self):
        path = self._write(
            json.dumps(_muse_settings(EXISTING_PROXY_URL)))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "muse-code", PROXY_URL, self.home)
        # Plain words: the refusal names the existing proxy.
        self.assertIn(EXISTING_PROXY_URL, str(ctx.exception))
        # Nothing changed, and no backup was made.
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_connect_refuses_missing_config(self):
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "muse-code", PROXY_URL, self.home)
        self.assertIn("Muse Code", str(ctx.exception))

    def test_muse_code_connect_refuses_non_dict_config(self):
        path = self._write(json.dumps(["not", "a", "dict"]))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused):
            connect_module.connect_agent(
                "muse-code", PROXY_URL, self.home)
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_connect_refuses_missing_schema_version(self):
        settings = _muse_settings()
        del settings["schema_version"]
        path = self._write(json.dumps(settings))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "muse-code", PROXY_URL, self.home)
        self.assertIn("schema_version", str(ctx.exception))
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_connect_refuses_wrong_schema_version(self):
        path = self._write(
            json.dumps(_muse_settings(schema_version=2)))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "muse-code", PROXY_URL, self.home)
        self.assertIn("schema_version", str(ctx.exception))
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_connect_refuses_non_dict_endpoint_transport(self):
        settings = _muse_settings()
        settings["endpoint_transport"] = "not-a-dict"
        path = self._write(json.dumps(settings))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "muse-code", PROXY_URL, self.home)
        self.assertIn("endpoint_transport", str(ctx.exception))
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_connect_refuses_when_backup_already_exists(self):
        path = self._write(json.dumps(_muse_settings()))
        connect_module.connect_agent(
            "muse-code", PROXY_URL, self.home)
        # The user removes the proxy by hand, so a second
        # connect would proceed, but the backup is still there.
        settings = json.loads(self._read(path))
        del settings["endpoint_transport"]
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(settings))
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "muse-code", PROXY_URL, self.home)
        self.assertIn("backup", str(ctx.exception).lower())
        # Nothing changed, and the original backup is intact.
        self.assertEqual(self._read(path), json.dumps(settings))
        self.assertEqual(
            self._read(path + ".iris-backup"),
            json.dumps(_muse_settings()))

    def test_muse_code_connect_refuses_when_config_changes_mid_connect(self):
        from unittest import mock
        path = self._write(json.dumps(_muse_settings()))
        changed = json.dumps(
            {**_muse_settings(), "model": "other-model"})
        calls = []
        real_read_bytes = connect_module._read_bytes

        def _changing_read(path_arg):
            data = real_read_bytes(path_arg)
            calls.append(data)
            if len(calls) == 1:
                # The agent rewrites its config after Iris read it.
                with open(path_arg, "w", encoding="utf-8") as f:
                    f.write(changed)
            return data

        with mock.patch.object(
                connect_module, "_read_bytes",
                side_effect=_changing_read):
            with self.assertRaises(connect_module.Refused) as ctx:
                connect_module.connect_agent(
                    "muse-code", PROXY_URL, self.home)
        self.assertIn("changed", str(ctx.exception).lower())
        # The agent's change is intact, and no backup was made.
        self.assertEqual(self._read(path), changed)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_connect_refuses_non_loopback_proxy_url(self):
        path = self._write(json.dumps(_muse_settings()))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused):
            connect_module.connect_agent(
                "muse-code", "http://proxy.example.com:8080",
                self.home)
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_disconnect_restores_backup(self):
        path = self._write(json.dumps(_muse_settings()))
        original = self._read(path)
        connect_module.connect_agent(
            "muse-code", PROXY_URL, self.home)
        self.assertNotEqual(self._read(path), original)
        result = connect_module.disconnect_agent(
            "muse-code", self.home)
        # The settings are back to the original content, byte
        # for byte.
        self.assertEqual(self._read(path), original)
        self.assertIn("Muse Code", result)
        # The backup is consumed, so a second disconnect refuses.
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_muse_code_disconnect_refuses_when_not_connected(self):
        path = self._write(json.dumps(_muse_settings()))
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.disconnect_agent(
                "muse-code", self.home)
        self.assertIn("Muse Code", str(ctx.exception))
        # The settings are untouched.
        self.assertEqual(
            self._read(path), json.dumps(_muse_settings()))

    def test_muse_code_config_path_is_under_home(self):
        path = connect_module.config_path("muse-code", self.home)
        self.assertTrue(
            os.path.abspath(path).startswith(
                os.path.abspath(self.home)),
            f"muse-code path escapes home: {path}")
        self.assertTrue(path.endswith("settings.json"))

    def test_muse_code_connect_result_does_not_leak_secrets(self):
        # The settings hold an mcp_servers entry with an auth
        # header. The result must not carry it: the read-back
        # names the proxy only, never the whole config.
        path = self._write(json.dumps(_muse_settings()))
        result = connect_module.connect_agent(
            "muse-code", PROXY_URL, self.home)
        self.assertNotIn("sk-test-only", result)
        self.assertNotIn("Bearer", result)
        self.assertIn(PROXY_URL, result)

    def test_muse_code_restart_returns_manual_instruction(self):
        # Muse Code is a terminal CLI, so Iris cannot restart it
        # the way it restarts Cursor. The restart returns the
        # manual instruction, not the Cursor wording.
        result = restart_module.restart_agent("muse-code")
        self.assertIn("Muse Code", result)
        self.assertIn("terminal", result.lower())
        self.assertNotIn("Cursor", result)

    def test_muse_code_is_in_agent_names(self):
        # The Connect dialog lists every agent in AGENT_NAMES, so
        # Muse Code shows up there with no UI change.
        self.assertIn("muse-code", connect_module.AGENT_NAMES)
        self.assertEqual(
            connect_module.AGENT_NAMES["muse-code"], "Muse Code")


if __name__ == "__main__":
    unittest.main()
