# Bridge prompt: implement the Iris app-mode shell from the design research

## Problem, one sentence

Iris has a dev panel and a packaged shell, but no consumer app mode: a native window a non-developer can open, watch their agent, and stop it in plain language.

## Intention / done definition

Implement the app-mode shell from the design research, as a first pass that we will deslopify and turn into a prescriptive spec before the final implementation. Done means: a native PySide6 window opens, embeds the existing HTML panel in a `QWebEngineView` loaded from the loopback panel server, shows native Verify, Pause, and Emergency stop controls outside the web view, starts the proxy and panel server in-process on a worker thread and stops them explicitly on close, shows the Simple tier by default with Advanced behind one switch and no Developer tier in the consumer build, and lands on a first-run screen in plain language with one click to run the demo. Tests-first: write the failing tests, Charles reviews the tests, then implement. Report as own commit. Branch only.

## Research, look first

Read the design research first: `workspace/goals/agent-network-observability-project/files/iris-app-mode-design-research.md` (filed Nov 22, 2026). It is the source for every decision below. Do not re-research the dashboard, packaging, installer, first-run, or tiers from the web; the research is done and its sources are labelled. If a decision in the research is marked Undecided, say so in the report and pick the research's recommended default, do not invent a new answer.

Key decisions from the research (do not re-decide them):

- **Dashboard:** hybrid (b), native PySide6 shell + embedded existing HTML panel via `QWebEngineView`, loaded from the loopback server (`http://127.0.0.1:<port>/...`), not via `setHtml()` (2 MB limit). Do not rebuild in native widgets, do not switch to Electron/Tauri.
- **Safety controls:** Verify, Pause, and Emergency stop are native Qt controls outside the web view, so they work even if Chromium or the panel server fails. Native only, one authoritative control; the HTML panel must not be the only path.
- **Proxy lifecycle:** proxy and panel server start in-process on a dedicated worker thread (`QThread`) owned by the main window, started after the UI shows a splash/loading state, stopped explicitly in `closeEvent`. Ask the thread to stop, wait with a timeout, then exit. A hung proxy must not hang quit, and a quit must not orphan a listening proxy. Loopback only (`127.0.0.1`), never `0.0.0.0`.
- **Tiers:** Simple by default, Advanced behind one explicit switch in Settings, Developer removed entirely from the consumer build. The dev panel is the Developer tier and stays a separate artifact.
- **First run:** one screen, plain language, no account. One click "Run the demo" (the loopback demo Run, $0.00), then land on the dashboard, not a tour. Empty states carry the teaching.
- **Packaging:** already decided and built (PyInstaller `--onedir` + Inno Setup, PR #43). Do not redo it. The shell must run from the packaged build and from source.
- **Teardown:** the `closeEvent` fix from PR #52 applies here too: stop the controller, remove the view container from the layout and delete it synchronously, then delete the view. Container first, never reverse. Do not regress it.

## Method

1. Read the design research file in full, then read the existing dev panel and the app-mode shell code on this branch to see what already exists. Do not write code before reading.
2. Write the failing tests first. Charles reviews the tests before implementation. Do not implement before he approves the tests.
3. Implement the shell: native window, `QWebEngineView` embedding the panel from loopback, native safety controls, proxy lifecycle on a worker thread, Simple/Advanced tiers, first-run screen. Keep the existing panel byte-for-byte; do not restyle it.
4. Before your final verification, run `git fetch origin` FIRST, then rebase the branch onto origin/main and run the tests on the rebased branch. A rebase onto a stale ref is no rebase at all. Report the new base commit.
5. Run the full suite, ruff, and scoped flake8 on the rebased branch. All must pass.
6. Write the report to `prompts/app-mode-design-report.md` on the branch, own commit. Name the new base commit, the tests, the decisions you made where the research was Undecided, and what is still open.

## Decisions

- This is a first pass. The designs are scratch paper. After this run, Main will deslopify them and write a prescriptive spec, and a second run will implement prescriptively. Do not polish this pass into a final design.
- Branch only. No PR, no merge. Merging needs Charles's words.
- Test prompt filenames must match `*-prompt.md`.
- If a decision in the research is marked Undecided, pick the research's recommended default and flag it in the report. Do not invent a new answer and do not re-research it from the web.

## Trajectory

Report the new base commit, the test counts, the lint results, the decisions made where the research was Undecided, and what is still open. Then stop.
