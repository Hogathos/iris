# App mode packaging — full-context Bridge prompt

Implement app-mode packaging from the spec. Read the spec first, then the rules, then the code.

## Read

- `specs/app-mode-packaging/requirements.md` — the spec. It is the success criteria. The smoke test is the oracle.
- `AGENTS.md` — the working rules. Note the `iris.spec` exception (Nov 24, 2026): the checked-in `iris.spec` is a hand-written source file, not a generated artifact. A generated `.spec` from a bare `pyinstaller` run stays banned.
- `appmode/__main__.py`, `appmode/shell.py`, `appmode/connect.py`, `appmode/restart.py` — the code being packaged. The entry point is `appmode/__main__.py`.
- The packaging plan is in the goal files, not the repo: the decisions are restated in the spec's Decisions section. Use the spec.

## Do

1. Write `iris.spec`: PyInstaller `--onedir` `--noconsole`, entry `appmode/__main__.py`, hidden imports and data files named in the spec. Relative paths only, so the spec is portable. No UPX. Use `sys._MEIPASS` for bundled resources where the code needs it.
2. Write `installer.iss`: Inno Setup, per-user install to `%LOCALAPPDATA%\Programs\Iris`, data folder separate at `%LOCALAPPDATA%\Iris Guardian\`, registered uninstaller, single `.exe` setup. Build it with `iscc`.
3. Write the smoke test: a script plus a documented manual run that proves, end to end, build → install → launch → connect. The smoke test checks: the built exe launches the Iris window with no console window; the top bar (Verify, Pause, Emergency stop, Connect) and the panel show; the proxy and panel start in-process; Connect connects an agent and the restart dialog works; data lives outside the bundle at `%LOCALAPPDATA%\Iris Guardian\`.
4. Build from a clean venv with pinned PyInstaller and PySide6. Run the smoke test. All of it must pass.

## Rules

- The packaged exe must never show a terminal or console window. This is the hard requirement. Prove it by launching the built exe and confirming no console appears.
- Do not put dev tools, test buttons, or diagnostics in the consumer app. The consumer app is the native window, plain dashboard, buttons on top, run table below.
- Out of scope: CI packaging build, Winget manifest, code signing, auto-update, MSIX. Do not build those.
- Commit named files only. Never `git add -A`, never `git commit -a`. Never commit `.exe`, `dist/`, `build/`, `*.db`, `.pytest_cache/`, or secrets. The checked-in `iris.spec` is the one exception (see AGENTS.md).
- Push the branch only. Do NOT open a PR or merge.
- Run the full pytest suite (`python -m pytest tests/ -q`) plus `ruff check .`, `flake8 --select=E9,F63,F7,F82 .`, and pyright. All must pass. A packaging change must not break the suite.
- Report the commit hash, the build result, the smoke test result, and your session usage (Input, Cached, Output, Total, Turns, Current %, Weekly %) from the usage panel. Then stop.
