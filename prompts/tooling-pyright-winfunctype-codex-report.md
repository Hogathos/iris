# PR #68 Pyright fix

**Correct.** Pyright reports no diagnostics in tracked project source on all three platform targets. The suite matches the baseline, and the Windows callback runs with identical Python syntax.

## Cause and fix

Before `855c35c`, the `WINFUNCTYPE` access and its line-scoped ignore shared a line. The original file from the PR #67 merge commit `f7be149` passed Pyright 1.1.414 on the Linux target. Commit `855c35c` split the decorator across several lines. Ruff kept the ignore beside the first `ctypes.c_void_p` argument, two lines below the access. On the Linux target, the same Pyright version reproduced `reportAttributeAccessIssue` at `smoke_packaging.py:94:13` before the fix.

At `smoke_packaging.py:94`, move `# pyright: ignore[reportAttributeAccessIssue]` from the first argument to the `@ctypes.WINFUNCTYPE(` line. The existing User32 lookup already returns `False` on `AttributeError` before the decorator runs. The comment move restores the original line-scoped suppression.

## Verification

`pyrightconfig.json` uses basic mode and Python 3.10. It checks project source and excludes `tests` and `locustfile.py`. The minimal, quick, and full CI jobs install Pyright without a version pin. Each runs bare `pyright` after the Ruff checks.

| Check | Result |
| --- | --- |
| `ruff check .` | All checks passed. |
| `ruff format --check .` | 220 files already formatted. |
| Pyright 1.1.414 on `smoke_packaging.py` | Linux, Darwin, and Windows targets each returned 0 errors, 0 warnings, 0 informations. |
| Pyright 1.1.414 on all tracked Python source within the project exclusions | Linux, Darwin, and Windows targets each returned 0 errors, 0 warnings, 0 informations with the `.venv314` interpreter selected explicitly. |
| Default workspace Pyright scan | 25 errors, 1 warning, 0 informations on each platform target. It picked up ignored watcher probes and could not resolve the installed PySide6, shiboken6, and PyYAML dependencies. Selecting `.venv314` explicitly on the Linux target reduced this to 9 errors in the probes and 0 warnings. No diagnostics in `smoke_packaging.py`. |
| `python -m pytest tests/ -q` | Exit 0. `455 passed, 1 warning, 51 subtests passed in 223.77s (0:03:43)`. The warning came from the local `.pytest_cache` path. The first run reached 100% but exited with `WinError 5` while cleaning up the default `pytest-current` path. The full rerun used a fresh `--basetemp` inside `.venv314` through `PYTEST_ADDOPTS`. |
| Runtime read-back | The Python syntax tree matches HEAD. On Windows, the helper called User32 and returned `False` for an absent title. With User32 unavailable, it also returned `False`. |
| Seeded demo scripts | `visualizer.py` and `report.py` completed on a temporary database. Both reported 6 attempts, 2 successes, and 3 retries. |
| Secrets | Reviewed both deliverables for secrets. No secret values added. |

## Open verification

The platform targets ran on a Windows host. Native Linux and macOS CI and the full Windows packaging smoke run are unverified. The fix and report are in the working tree. The usage panel was unavailable, so Input, Cached, Output, Total, and Turns are unavailable.
