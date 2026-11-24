# App mode packaging — requirements

## What it does

Package the app-mode Iris window into a Windows exe and installer a user can download, install, and run. The packaged app is the consumer app: a native window with buttons on top and a run table below, no terminal, no dev tools.

## Acceptance criteria

1. `iris.spec` exists, checked in, and builds with PyInstaller `--onedir` `--noconsole` from a clean venv, with pinned PyInstaller and PySide6 versions. Entry point is `appmode/__main__.py`. Hidden imports and data files are named in the spec, not passed as bare CLI flags.
2. The built exe launches the Iris window. No console or terminal window appears behind it, ever.
3. The window shows the top bar (Verify, Pause, Emergency stop, Connect) and the panel. The proxy and panel start in-process; the panel loads.
4. Connect works in the packaged exe: connecting an agent (Claude Code, Cursor, or Continue) to the proxy succeeds, and the restart dialog appears and behaves as specified (Restart, "I'll save first", Cancel).
5. `installer.iss` exists, checked in, and builds a single `.exe` setup with Inno Setup (`iscc`). Per-user install to `%LOCALAPPDATA%\Programs\Iris`. A registered uninstaller is included. Installing then launching the installed exe satisfies criteria 2–4.
6. Data (logs, state, config) lives outside the bundle at `%LOCALAPPDATA%\Iris Guardian\`, not in the install folder. An update (re-running a newer installer) overwrites the bundle without touching user data.
7. A smoke test proves criteria 2–5 end to end: build, install, launch, connect. The smoke test is a script plus a documented manual run. It is the oracle for this feature.

## Oracle

The smoke test (build → install → launch → connect). It must pass end to end on Windows. A passing unit suite alone does not prove packaging works.

## Non-goals

- CI packaging build (GitHub Actions Windows runner). Distribution work, after v1.
- Winget manifest. Distribution work, after v1.
- Code signing (SignPath Foundation, OV cert). Later step, not v1.
- Auto-update (WinSparkle, Velopack). Deferred; v1 is manual download from GitHub Releases.
- MSIX. Optional last, and only after a loopback smoke test proves it (loopback is blocked by default for packaged apps).

## Decisions

- PyInstaller `--onedir`, not `--onefile` (onefile has the AV false-positive history and unpacks at runtime).
- `iris.spec` is checked in. Exception to the repo `*.spec` ban (Charles, Nov 24, 2026): a hand-written spec with relative paths is a source file, not a generated artifact. A generated `.spec` from a bare `pyinstaller` run stays banned.
- `--noconsole`: the packaged exe must never show a terminal or console window (Charles, 2026-11-22).
- No UPX on Qt binaries (it corrupts / slows Qt DLL loading).
- Data outside the bundle at `%LOCALAPPDATA%\Iris Guardian\` (product name is the intended mark, a candidate, not cleared; confirm before shipping paths users will see).
- Use `sys._MEIPASS` for bundled resources (PyInstaller 6.0+; `os.path.dirname(sys.executable)` breaks silently).
- Inno Setup, per-user install, single `.exe` setup. Manual updates v1.
- The app must NOT look like a Muse artifact page — no hero headline, no card grid, no CTA. Native window, plain dashboard: buttons on top, run table below (Charles, 2026-11-22).
- Public-facing screenshots, demos, and posts use app mode, not the dev panel (Charles, 2026-11-23).
