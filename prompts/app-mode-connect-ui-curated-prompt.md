# Connect UI — curated run (measurement only)

Implement the Connect button and agent-restart step for the app-mode window so the 14 tests in tests/test_appmode_connect_ui.py pass.

Read tests/test_appmode_connect_ui.py, appmode/shell.py, and appmode/connect.py. Add a Connect button to the top bar of the app-mode window. When clicked, pick an agent, connect it to the proxy, and ask whether to restart it. Cursor on Windows restarts for real; Claude Code and Continue get a manual instruction. The dialog and the button words are pinned by the tests; match them exactly.

Run `python -m pytest tests/test_appmode_connect_ui.py -q`, then the full suite `python -m pytest tests/ -q`, plus `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`, and pyright. All must pass.

Commit named files only. Push the branch only. Do NOT open a PR or merge. Report the commit hash and the test results, then stop.

This is a measurement run. Do not merge it. The feature is already on main; this branch is throwaway.
