# Bridge watcher: trust the workspace when launching Muse Code

## What happened

The watcher launches Muse Code headless to run a prompt file.
The run on Nov 24, 2026, after the PR #47 fix, started in the
right repo (`C:\Users\Quant\Documents\iris`) and streamed output
to the watcher terminal. But Muse Code printed this at startup:

```
muse: warning: rules file at \\?\C:\Users\Quant\Documents\iris\AGENTS.md exists, but the workspace is untrusted, so it is skipped for this session; restart with --trust-workspace to load project rules
muse: Agent delegation: auto unavailable: workspace is untrusted.
muse: warning: project MCP servers from .mcp.json, if any, are not loaded because the workspace is untrusted; restart with --trust-workspace to load them
```

The workspace is untrusted, so the run skips the repo rules in
`AGENTS.md`, cannot delegate to subagents, and does not load the
project MCP servers. The repo rules are the method: test-first,
the plan gate, the Co-Work safeguards. A watcher run without
them is not the same run as a hand-triggered run in PyCharm.

This is watcher Bug 5. Bugs 1-4 are in the mistakes log at
`~/workspace/mistakes-log.md` (not in the repo). The pattern is
the same as the earlier bugs: the runner's real invocation was
never exercised live against the real `muse` on Windows.

## The fix

`build_command` in `tools/bridge_watcher.py` builds the headless
command. It currently returns:

```python
["muse", "exec", "--prompt-file", prompt_path, "--disable-approval"]
```

Add `--trust-workspace` to that command, so Muse Code loads the
project rules, delegation, and project MCP servers for the repo
it runs in. The repo is the user's own repo on the user's own
machine; trusting it is the same trust already given in PyCharm.
Place the flag where the `muse` CLI accepts it; do not change
the other flags or their order.

## Tests first

Write the failing test first, following the existing patterns in
`tests/test_bridge_watcher.py`. `LaunchCommandTest` pins the exact
command; extend it (or add a test beside it) asserting the built
command includes `--trust-workspace`. Run the suite and show the
new test failing before the fix and passing after it.

## Rules

- Follow the repo rules in `AGENTS.md`: pytest only, tests
  committed separately from the fix, never edit tests during
  implementation. Report the commit hash, the push result, and
  the test results, then stop.
- Do not hardcode any path. The fix is the flag only.
- Push the branch only. Do not open a PR. Do not merge.
- This run is hand-triggered in PyCharm, not launched by the
  watcher. The watcher cannot fix itself through itself.
