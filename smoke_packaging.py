"""Packaging smoke test: build, install, launch, connect (oracle).

End to end proof that the packaged app works: the clean venv
builds, the exe has no console, the installer installs, the
installed exe opens the Iris window with the panel serving and
the proxy blocking, and user data lands outside the bundle.
The script proves what a script can prove. The visual proof
(the panel paints, no console window appears) and the Connect
flow need human eyes and hands: see smoke_packaging.md.

Run (from the repo root, plain python, not the build venv):
  python smoke_packaging.py [--skip-build] [--skip-install]

--skip-build reuses the last dist\\ build. --skip-install uses
the last installer-out\\ setup. Both default off: a full run
builds and installs fresh. Exit 0 means every automated check
passed. Anything else prints the failed step in plain words.

Pinned build tools (documented, installed into the clean venv):
  PyInstaller==6.22.3, PySide6==6.12.0. The rest comes from
  requirements.txt, same as CI.
"""

from __future__ import annotations

import argparse
import ctypes
import http.client
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
PIN_PYINSTALLER = "PyInstaller==6.22.3"
PIN_PYSIDE6 = "PySide6==6.12.0"

# Subsystem this exe must carry. 2 is windowed (no console),
# 3 is console. The hard requirement is 2, checked from the
# PE headers, no launch needed.
IMAGE_SUBSYSTEM_WINDOWS_GUI = 2

PANEL_TIMEOUT = 30.0
WINDOW_TIMEOUT = 60.0


def _say(text):
    print(f"smoke: {text}", flush=True)


def _fail(text):
    print(f"smoke FAILED: {text}", flush=True)
    return 1


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _pe_subsystem(exe_path):
    """The PE subsystem id: 2 means no console window, ever."""
    with open(exe_path, "rb") as handle:
        data = handle.read(512)
    if data[:2] != b"MZ":
        raise ValueError("not an exe file")
    pe_offset = struct.unpack("<I", data[0x3C:0x40])[0]
    with open(exe_path, "rb") as handle:
        handle.seek(pe_offset + 4 + 20)
        (magic,) = struct.unpack("<H", handle.read(2))
        handle.seek(pe_offset + 4 + 20 + 68)
        (subsystem,) = struct.unpack("<H", handle.read(2))
    if magic not in (0x10B, 0x20B):
        raise ValueError("not a PE32/PE32+ file")
    return subsystem


def _window_visible(title):
    """True when a top-level window carries this exact title."""
    try:
        user32 = ctypes.windll.user32  # pyright: ignore[reportAttributeAccessIssue]
    except AttributeError:
        return False
    found = []

    @ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,  # pyright: ignore[reportAttributeAccessIssue]
        ctypes.c_void_p,
    )
    def _each(hwnd, _arg):
        length = user32.GetWindowTextLengthW(hwnd)
        if length == len(title):
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value == title and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    user32.EnumWindows(_each, None)
    return bool(found)


def _wait_for_window(title, timeout):
    """True when the window appears in time."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _window_visible(title):
            return True
        time.sleep(1.0)
    return False


def _http_status(host, port, path):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        return conn.getresponse().status
    finally:
        conn.close()


def _http_body(host, port, path):
    with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=5) as resp:
        return resp.status, resp.read().decode()


def _run(cmd, cwd=None, env=None, timeout=1800):
    """Run to completion. Timeout in seconds, generous for builds."""
    proc = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args(argv)

    sandbox = tempfile.mkdtemp(prefix="iris-smoke-")
    venv_dir = os.path.join(sandbox, "venv")
    _say(f"sandbox at {sandbox}")

    # A clean venv, so the build proves its own deps, not mine.
    if not args.skip_build:
        _say("creating the clean venv")
        code, out = _run([sys.executable, "-m", "venv", venv_dir], timeout=600)
        if code != 0:
            return _fail(f"venv failed: {out[-2000:]}")
        pip = os.path.join(venv_dir, "Scripts", "pip.exe")
        _say(f"installing {PIN_PYINSTALLER} and {PIN_PYSIDE6}")
        code, out = _run(
            [pip, "install", PIN_PYINSTALLER, PIN_PYSIDE6, "-r", os.path.join(REPO, "requirements.txt")], timeout=1800
        )
        if code != 0:
            return _fail(f"pip install failed: {out[-2000:]}")

        # The build itself, from the repo root, using iris.spec.
        # --noconfirm replaces a previous dist output, so a re-run
        # never stops on a leftover build folder.
        pyinstaller = os.path.join(venv_dir, "Scripts", "pyinstaller.exe")
        _say("running pyinstaller iris.spec")
        code, out = _run([pyinstaller, "--noconfirm", "iris.spec"], cwd=REPO, timeout=2400)
        if code != 0:
            return _fail(f"pyinstaller failed: {out[-4000:]}")

    dist_exe = os.path.join(REPO, "dist", "Iris", "Iris.exe")
    if not os.path.exists(dist_exe):
        return _fail(f"no built exe at {dist_exe}")

    # No console, proven from the headers, no launch needed.
    subsystem = _pe_subsystem(dist_exe)
    if subsystem != IMAGE_SUBSYSTEM_WINDOWS_GUI:
        return _fail(f"built exe subsystem is {subsystem}, not windowed (2)")
    _say("built exe is windowed: no console, ever")

    # The installer, if iscc is on PATH. Building it proves
    # installer.iss parses and the bundle packs.
    setup_exe = None
    if not args.skip_install:
        iscc = shutil.which("iscc")
        if iscc is None:
            return _fail("iscc not found: install Inno Setup 6 first")
        _say("building the installer with iscc")
        code, out = _run([iscc, "installer.iss"], cwd=REPO, timeout=1200)
        if code != 0:
            return _fail(f"iscc failed: {out[-4000:]}")
        out_dir = os.path.join(REPO, "installer-out")
        setups = sorted(f for f in os.listdir(out_dir) if f.endswith(".exe"))
        if not setups:
            return _fail("iscc built no setup exe")
        setup_exe = os.path.join(out_dir, setups[-1])
        _say(f"installer built: {setups[-1]}")

        # Silent install to a temp folder, so the smoke run never
        # touches a real install. The data folder still goes to
        # the real per-user home (Inno resolves it, not the env),
        # which is exactly what the manual run checks.
        install_dir = os.path.join(sandbox, "install")
        _say("installing silently to a temp folder")
        code, out = _run(
            [
                setup_exe,
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/DIR={install_dir}",
                f"/LOG={os.path.join(sandbox, 'install.log')}",
            ],
            timeout=600,
        )
        if code != 0:
            return _fail(f"silent install failed: {out[-2000:]}")
    else:
        install_candidates = []
        out_dir = os.path.join(REPO, "installer-out")
        if os.path.isdir(out_dir):
            install_candidates = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".exe"))
        if not install_candidates:
            return _fail("skip-install needs a setup exe already")
        setup_exe = install_candidates[-1]
        install_dir = os.path.join(sandbox, "install")
        code, out = _run([setup_exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={install_dir}"], timeout=600)
        if code != 0:
            return _fail(f"silent install failed: {out[-2000:]}")

    installed_exe = os.path.join(install_dir, "Iris.exe")
    if not os.path.exists(installed_exe):
        return _fail(f"no installed exe at {installed_exe}")
    if _pe_subsystem(installed_exe) != IMAGE_SUBSYSTEM_WINDOWS_GUI:
        return _fail("installed exe is not windowed")
    _say("installed exe is windowed too")

    # Launch the installed app on throwaway ports. LOCALAPPDATA
    # points at the sandbox, so the journal and the lock stay
    # out of the real profile.
    panel_port = _free_port()
    proxy_port = _free_port()
    sandbox_data = os.path.join(sandbox, "LocalAppData")
    os.makedirs(sandbox_data, exist_ok=True)
    env = dict(os.environ)
    env["LOCALAPPDATA"] = sandbox_data
    _say(f"launching on panel {panel_port}, proxy {proxy_port}")
    child = subprocess.Popen(
        [installed_exe, "--panel-port", str(panel_port), "--proxy-port", str(proxy_port)], env=env, cwd=sandbox_data
    )
    try:
        if not _wait_for_window("Iris", WINDOW_TIMEOUT):
            return _fail("the Iris window never appeared")
        _say("the Iris window is open")

        status, body = _http_body("127.0.0.1", panel_port, "/?tier=simple")
        if status != 200 or "Runs, live" not in body:
            return _fail("the panel did not serve Runs, live")
        _say("the panel serves Runs, live")

        conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
        try:
            conn.request("GET", "http://example.com/")
            proxy_status = conn.getresponse().status
        finally:
            conn.close()
        if proxy_status != 403:
            return _fail(f"the proxy answered {proxy_status}, not 403")
        _say("the proxy blocks example.com with 403")

        journal = os.path.join(sandbox_data, "Iris Guardian", "attempts.db")
        if not os.path.exists(journal):
            return _fail(f"no journal at {journal}: data is not outside the bundle")
        _say("the journal lives outside the bundle")
        install_db = os.path.join(install_dir, "attempts.db")
        if os.path.exists(install_db):
            return _fail("journal written inside the install folder")
        _say("nothing was written inside the install folder")
    finally:
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
    _say("the app stopped with the window")

    _say("ALL SMOKE CHECKS PASS. Manual run next: see smoke_packaging.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
