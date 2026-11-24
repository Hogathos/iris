# Packaging smoke run — manual part

The script `smoke_packaging.py` proves what a script can prove:
the clean-venv build, the windowed exe, the silent install, the
open window, the serving panel, the blocking proxy, and data
outside the bundle. A human proves the rest, once, by eye and
by hand. This file is that manual run.

## Setup

1. Run the script from the repo root: `python smoke_packaging.py`.
   It ends with ALL SMOKE CHECKS PASS, or names the failed step.
2. Install Inno Setup 6 first (`iscc` must be on PATH), or the
   script stops at the installer step and says so.

## Manual run (about ten minutes)

1. Install for real: run the setup exe from `installer-out\`
   with no flags. Click through. The default folder is
   `%LOCALAPPDATA%\Programs\Iris`. A desktop shortcut is
   offered, unchecked by default.
2. Launch Iris from the Start menu. Confirm with your eyes:
   no console or terminal window appears behind it, ever.
   The taskbar shows one Iris window.
3. The window is 1024 by 768. The top bar reads Verify, Pause,
   Emergency stop, Connect, and the Advanced checkbox. Below
   it the panel loads: "Runs, live", the filter form, the
   run table. The dashboard paints inside the window.
4. Click Connect. Pick Continue (or any known agent). The
   result dialog shows plain words: the agent, the proxy,
   where the backup is. No whole config, no secret.
5. The restart warning appears with the exact words from the
   spec. Press "I'll save first": nothing restarts. Click
   Connect again, confirm with Restart: the agent restarts
   (Cursor on Windows), or the manual instruction shows.
   Press Cancel on a fresh connect: nothing restarts.
6. Disconnect the agent (dev panel or a fresh backup flow):
   the config is restored byte for byte.
7. Check the data folder: `%LOCALAPPDATA%\Iris Guardian\`
   holds `attempts.db`. The install folder holds no journal,
   no lock file, nothing written.
8. Uninstall from Settings, Apps. The bundle goes away. The
   data folder stays: user data is never deleted by setup.
   Re-run the installer to confirm an update overwrites the
   bundle and keeps the data.

## Expected result

Every step above behaves as written. Any deviation is a
packaging defect: file it, do not ship around it.
