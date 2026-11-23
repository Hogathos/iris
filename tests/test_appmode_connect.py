"""One-click connect tests: Iris writes an agent's proxy setting.

Iris connects a known agent to the Iris proxy with one click. Iris
backs up the agent's config before it writes, shows the user what
changed, and restores the backup on disconnect or clean exit.

These tests are the contract for appmode/connect.py, which does not
exist yet. They fail until the module is implemented. The test
writer is not the implementer.

Agents in v1: Claude Code, Cursor, Continue. Each has a clean
config-file path for the proxy setting. Research:
one-click-connect-agent-config-research.md.

Rules under test (from specs/app-mode-connect/requirements.md):
- Iris backs up the config before it writes.
- Iris writes the proxy setting, then reads the file back.
- Iris shows the user the change in plain words.
- If the agent already has a proxy setting, Iris refuses in plain
  words and changes nothing.
- If the agent is not in the known list, Iris says so in plain words.
- One click disconnects. Iris restores the backup.
- Every agent needs a restart to pick up a proxy change. The
  restart is part of the flow, not part of these tests.
- The result never carries a secret. The read-back names the
  proxy only, never the whole config (review pass, Nov 23).
- A Continue config with no models has nothing to point at the
  proxy. Iris refuses instead of reporting a success that
  changed nothing (review pass, Nov 23).
- An existing NO_PROXY or noProxy list gains localhost and
  127.0.0.1 when they are missing. The agent must not send
  Iris's own loopback traffic back through the proxy (review
  pass, Nov 23).
- A config that parses but is not a dict is broken. Iris refuses
  instead of replacing it (review pass, Nov 23).

Tests use a temp home dir, never the real one. Config paths come
from the module, given a home dir, so the same test runs on all
three OSes in CI.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from appmode import connect as connect_module

# The proxy Iris writes: its own loopback address.
PROXY_URL = "http://127.0.0.1:8888"

# A proxy the user already had, so the refuse path is exercised.
EXISTING_PROXY_URL = "http://proxy.example.com:8080"


def _claude_settings(proxy=None):
    """A Claude Code settings.json dict, with or without a proxy."""
    settings = {"theme": "dark"}
    if proxy is not None:
        settings["env"] = {
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "NO_PROXY": "localhost,127.0.0.1",
        }
    return settings


def _cursor_settings(proxy=None):
    """A Cursor user settings.json dict, with or without a proxy."""
    settings = {"editor.fontSize": 14}
    if proxy is not None:
        settings["http.proxy"] = proxy
        settings["http.proxySupport"] = "override"
    return settings


def _continue_config(proxy=None):
    """A Continue config.yaml dict, with or without a proxy.

    The module reads YAML. The test writes it as JSON here only
    because the shape is a plain dict; the module must parse real
    YAML, not JSON.
    """
    model = {
        "name": "GPT-4o",
        "provider": "openai",
        "model": "gpt-4o",
        "apiKey": "sk-test-only",
        "roles": ["chat"],
    }
    if proxy is not None:
        model["requestOptions"] = {"proxy": proxy}
    return {"models": [model]}


class ConnectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, agent_id, content):
        """Write a config file for an agent, return its path."""
        path = connect_module.config_path(agent_id, self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_claude_connect_writes_proxy_and_backs_up(self):
        path = self._write(
            "claude-code", json.dumps(_claude_settings()))
        original = self._read(path)
        result = connect_module.connect_agent(
            "claude-code", PROXY_URL, self.home)
        # The config now carries the Iris proxy.
        settings = json.loads(self._read(path))
        self.assertEqual(
            settings["env"]["HTTP_PROXY"], PROXY_URL)
        self.assertEqual(
            settings["env"]["HTTPS_PROXY"], PROXY_URL)
        # The backup holds the original content, byte for byte.
        self.assertEqual(
            self._read(path + ".iris-backup"), original)
        # The result is plain words: agent, file, change.
        self.assertIn("Claude Code", result)
        self.assertIn(path, result)
        self.assertIn(PROXY_URL, result)

    def test_cursor_connect_writes_proxy_and_backs_up(self):
        path = self._write(
            "cursor", json.dumps(_cursor_settings()))
        original = self._read(path)
        result = connect_module.connect_agent(
            "cursor", PROXY_URL, self.home)
        settings = json.loads(self._read(path))
        self.assertEqual(settings["http.proxy"], PROXY_URL)
        self.assertEqual(
            settings["http.proxySupport"], "override")
        self.assertEqual(
            self._read(path + ".iris-backup"), original)
        self.assertIn("Cursor", result)
        self.assertIn(PROXY_URL, result)

    def test_continue_connect_writes_proxy_and_backs_up(self):
        path = self._write(
            "continue", json.dumps(_continue_config()))
        original = self._read(path)
        result = connect_module.connect_agent(
            "continue", PROXY_URL, self.home)
        # The module parses YAML, so read the file back as YAML
        # through the module's own reader, not as JSON.
        config = connect_module.read_config("continue", path)
        self.assertEqual(
            config["models"][0]["requestOptions"]["proxy"],
            PROXY_URL)
        self.assertEqual(
            self._read(path + ".iris-backup"), original)
        self.assertIn("Continue", result)
        self.assertIn(PROXY_URL, result)

    def test_connect_refuses_when_proxy_already_set(self):
        path = self._write(
            "claude-code",
            json.dumps(_claude_settings(EXISTING_PROXY_URL)))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "claude-code", PROXY_URL, self.home)
        # Plain words: the refusal names the existing proxy.
        self.assertIn(EXISTING_PROXY_URL, str(ctx.exception))
        # Nothing changed, and no backup was made.
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_connect_refuses_unknown_agent(self):
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "not-an-agent", PROXY_URL, self.home)
        self.assertIn("not-an-agent", str(ctx.exception))

    def test_connect_refuses_missing_config(self):
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "claude-code", PROXY_URL, self.home)
        self.assertIn("Claude Code", str(ctx.exception))

    def test_disconnect_restores_backup(self):
        path = self._write(
            "claude-code", json.dumps(_claude_settings()))
        original = self._read(path)
        connect_module.connect_agent(
            "claude-code", PROXY_URL, self.home)
        self.assertNotEqual(self._read(path), original)
        result = connect_module.disconnect_agent(
            "claude-code", self.home)
        # The config is back to the original content, byte for byte.
        self.assertEqual(self._read(path), original)
        self.assertIn("Claude Code", result)

    def test_disconnect_refuses_when_not_connected(self):
        path = self._write(
            "claude-code", json.dumps(_claude_settings()))
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.disconnect_agent(
                "claude-code", self.home)
        self.assertIn("Claude Code", str(ctx.exception))
        # The config is untouched.
        self.assertEqual(
            self._read(path), json.dumps(_claude_settings()))

    def test_config_path_is_under_home(self):
        # The path must stay inside the given home dir, never the
        # real one, so tests and the app never touch a live config.
        for agent_id in ("claude-code", "cursor", "continue"):
            path = connect_module.config_path(agent_id, self.home)
            self.assertTrue(
                os.path.abspath(path).startswith(
                    os.path.abspath(self.home)),
                f"{agent_id} path escapes home: {path}")

    def test_connect_result_does_not_leak_secrets(self):
        # The Continue config holds a model apiKey. The result
        # must not carry it: the read-back names the proxy only,
        # never the whole config.
        path = self._write(
            "continue", json.dumps(_continue_config()))
        result = connect_module.connect_agent(
            "continue", PROXY_URL, self.home)
        self.assertNotIn("sk-test-only", result)
        self.assertIn(PROXY_URL, result)

    def test_continue_connect_refuses_when_no_models(self):
        # A Continue config with no models has nothing to point
        # at the proxy. Iris refuses in plain words instead of
        # reporting a success that changed nothing.
        path = self._write("continue", json.dumps({"models": []}))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused) as ctx:
            connect_module.connect_agent(
                "continue", PROXY_URL, self.home)
        self.assertIn("Continue", str(ctx.exception))
        # Nothing changed, and no backup was made.
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))

    def test_claude_connect_appends_loopback_to_no_proxy(self):
        # An existing NO_PROXY without localhost must gain it: the
        # agent must not send Iris's own loopback traffic back
        # through the proxy.
        settings = _claude_settings()
        settings["env"] = {"NO_PROXY": "example.com"}
        path = self._write("claude-code", json.dumps(settings))
        connect_module.connect_agent(
            "claude-code", PROXY_URL, self.home)
        settings = json.loads(self._read(path))
        no_proxy = settings["env"]["NO_PROXY"]
        self.assertIn("example.com", no_proxy)
        self.assertIn("localhost", no_proxy)
        self.assertIn("127.0.0.1", no_proxy)

    def test_continue_connect_appends_loopback_to_no_proxy(self):
        # An existing noProxy list without localhost must gain it.
        config = _continue_config()
        config["models"][0]["requestOptions"] = {
            "noProxy": ["example.com"]}
        path = self._write("continue", json.dumps(config))
        connect_module.connect_agent(
            "continue", PROXY_URL, self.home)
        config = connect_module.read_config("continue", path)
        no_proxy = config["models"][0]["requestOptions"]["noProxy"]
        self.assertIn("example.com", no_proxy)
        self.assertIn("localhost", no_proxy)
        self.assertIn("127.0.0.1", no_proxy)

    def test_connect_refuses_non_dict_config(self):
        # A config that parses but is not a dict is broken. Iris
        # refuses instead of replacing it with a fresh dict.
        path = self._write(
            "claude-code", json.dumps(["not", "a", "dict"]))
        original = self._read(path)
        with self.assertRaises(connect_module.Refused):
            connect_module.connect_agent(
                "claude-code", PROXY_URL, self.home)
        self.assertEqual(self._read(path), original)
        self.assertFalse(os.path.exists(path + ".iris-backup"))


if __name__ == "__main__":
    unittest.main()
