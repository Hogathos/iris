# App mode shell build notes

Author: Muse Code, agent, implementation only
Decided by: tests (another agent wrote them, Charles reviews)
Filed by: Muse Code, agent
Tags: agent-written, test-defined

Date: 2026-11-23
Status: built, sandbox-blocked on 10 of 25 new tests (see below)

## Problem
The branch held the spec and 24 failing tests for the App mode
shell. The shell had to satisfy the tests exactly as written.

## Choices made during the build

### 1. Single-instance lock beside the journal
Choice: the lock file lives next to the journal file, named
`<db>.iris-<instance>.lock`, with a loopback beacon port inside.
Rejected: a lock in the system temp folder or a fixed port per name.
Why: the proxy must write the journal, so the journal folder is
writable by definition. No new folder, no new permission to explain.

### 2. View stays a bare QWebView member
Choice: `IrisWindow.view` is a plain QWebView, not embedded in the
widget layout. Rejected: `QWidget.createWindowContainer(view)`.
Why: the offscreen platform plugin has no setParent, and the call
segfaults there (exit 0xC0000005), which would crash CI too. The
view still loads the panel on setUrl. Pixel proof of the embedded
dashboard needs a real display.

### 3. Journal handle closes on stop
Choice: `AppController.stop` closes the proxy state's sqlite handle
after the serving threads join. Rejected: leave it for the process
to drop. Why: on Windows the open handle locks the file, so a
caller that removes the folder (tests do) hits PermissionError.

## Sandbox blocks (not code defects)
- Two controller assertions need `ConnectionRefusedError` on a
freed loopback port. This sandbox drops those SYNs: bare-socket
probes show even never-bound ports time out. Re-bind proves the
ports do free. Correct on a real machine.
- Eight WindowTest tests pump Qt events with a live QWebView. The
offscreen WebView2 backend access-violates on any event pump here,
with or without setUrl. Needs a real display (and CI Linux, where
the backend is WebEngine, may already pass).

## Links
Spec: specs/app-mode-shell/requirements.md. Tests:
tests/test_appmode_shell.py, tests/test_appmode_tiers.py. Package:
appmode/shell.py, appmode/__main__.py. Tier change:
devpanel/panel.py.
