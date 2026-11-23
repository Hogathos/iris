# App mode shell

## What it does

App mode is one window. The window shows the Iris dashboard. The
dashboard is the existing panel, inside the window, not in a browser.
The proxy starts inside the app. The human watches Runs, verifies
them, pauses them, and stops everything, all in the window.

## Success criteria

- One window opens. The dashboard loads from the local panel server.
- Verify, Pause, and Emergency stop are native buttons outside the
  web view. They work even if the web view fails.
- The proxy starts with the app and stops when the window closes. No
  crash on exit. No orphan proxy.
- Simple shows Run, Destination, What, Money spent, Verdict, Pause.
  Advanced adds Bytes, Seconds, Method, the cutoff form, and
  Redirect. The switch sits top-right.
- Block is in Simple. Redirect is in Advanced. The split file is the
  source of truth for the tiers.
- The app never binds off this machine. Loopback only. No bodies, no
  keys. Plain words only.
- The full suite stays green, plus new tests for the shell.

## Non-goals

- Packaging. No PyInstaller, no installer, no Winget. That is a later
  step.
- First-run flow. No one screen, no one-click demo. That is a later
  step.
- One-click connect. No config editing for known agents. That is a
  later step.
- Auto-update, signing, MSIX. All later.
- The ICP protocol. Research is running. Not this step.
- The devtool.exe. A separate artifact. Not this step.

## Decisions

- 2026-11-22, hybrid shell. Choice: native PySide6 shell with the
  existing HTML panel in a web view, loaded from the loopback server.
  Rejected: rebuild the dashboard in native widgets, switch to
  Electron or Tauri. Why: lowest effort, exact fidelity, one UI to
  maintain. Source: app-mode design research, section 1.
- 2026-11-22, safety controls native. Choice: Verify, Pause,
  Emergency stop as native buttons outside the web view. Rejected:
  web-only controls. Why: they must work even if the web view fails.
- 2026-11-22, new package. Choice: a new `appmode/` package for the
  consumer shell. Rejected: put the shell in `devpanel/`. Why: dev
  and consumer code must not mix. The repo rule keeps dev-only helpers
  separate from the consumer app.
- 2026-11-22, proxy in-process. Choice: proxy and panel server run in
  the app process on a worker thread, stopped in closeEvent. Rejected:
  a subprocess. Why: one exe to sign, one lifecycle, no orphan child.
  Source: research report, section 2.
- 2026-11-22, smaller engine first. Choice: spike QtWebView, the
  native web view, first. Build the shell on it if the spike passes.
  Fall back to WebEngine only if the spike fails. Rejected: accept
  WebEngine's 130 MB without trying the smaller route. Why: the panel
  is zero-JS server-rendered HTML, so it needs only a browser that
  loads pages and submits forms, which the native view does. The spike
  measures the real bundle size instead of guessing it. The one real
  trade is that the native view behaves differently per OS, WebView2
  on Windows, WKWebView on macOS, WebKitGTK on Linux, and the spike
  tests Windows and Linux first, macOS by CI. The engine must also
  hold on macOS and Linux. The platform matrix is the check. Linux
  matters because it is the accessible option for people without
  money. Charles has Ubuntu WSL but is not a daily Linux user.
  Charles's call.
- 2026-11-22, spike verdict: QtWebView loads the panel. Choice:
  build the shell on QtWebView as the one API. Rejected: WebEngine
  everywhere. Why: the spike passed, index and map load, titles read.
  On Linux QtWebView rides on WebEngine (plugin
  libqtwebview_webengine.so), so it saves nothing there; the size win
  is Windows and macOS only, where the system provides the engine.
  Packaging excludes WebEngine on Windows and macOS and includes it
  on Linux. QtWebView's API is thinner: setUrl not load,
  loadingChanged with QWebViewLoadingInfo, no toHtml. Enough for the
  panel, which is plain HTML forms.
- 2026-11-22, offscreen Qt in tests. Choice: the test module sets
  QT_QPA_PLATFORM=offscreen before importing Qt. Rejected: edit the
  CI workflow. Why: no workflow edit is needed, the tests run the same
  everywhere.
- 2026-11-22, proxy stop on close. Choice: ask the proxy thread to
  stop, wait with a timeout, then exit. Rejected: let Qt teardown
  destroy a running thread. Why: that exact bug crashed a comparable
  app on exit. Source: research report, section 2.
- 2026-11-22, text input native only. Choice: all text input lives in
  native dialogs, never in the web view. Rejected: type into the web
  view. Why: the web view has known focus and keyboard quirks. Source:
  research report, section 1.
- 2026-11-22, tier default. Choice: no tier passed means show
  everything. The dev panel works exactly as before. Rejected: the
  tier change alters the dev panel default. Why: the tier change must
  add, not change, the dev panel. The existing panel tests must stay
  green.
