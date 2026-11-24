# iris.spec — hand-written PyInstaller spec (source file, not generated).
#
# Exception to the repo *.spec ban (Charles, Nov 24, 2026): this
# file is written by hand with relative paths, so it builds on any
# machine. A generated .spec from a bare `pyinstaller` run stays
# banned: never check one in.
#
# Build (from the repo root, in the build venv):
#   pyinstaller iris.spec
#
# --onedir and --noconsole are set below, not as CLI flags. Hidden
# imports and data files live here, not on the command line.
# No UPX: it corrupts and slows Qt DLL loading.

import os
import sys

import PySide6

# The folder holding this file: the repo root. Every path below
# hangs off it, so the spec never names a machine.
REPO = SPECPATH

# The QtWebView backend plugin. PyInstaller's PySide6 hooks ship
# the standard plugin kinds but not plugins/webview, so without
# this the app prints "No WebView plug-in found!" and aborts.
# Windows rides on WebView2 (the system web view, small bundle);
# Linux rides on WebEngine. Only the running platform's backend
# ships: the unused engine stays out of the bundle.
_WEBVIEW_PLUGIN_DIR = os.path.join(
    os.path.dirname(PySide6.__file__), "plugins", "webview")
if sys.platform == "win32":
    _WEBVIEW_BACKEND = "qtwebview_webview2.dll"
else:
    _WEBVIEW_BACKEND = "qtwebview_webengine.dll"
# Dest is relative to the bundle content dir (COLLECT puts
# content under _internal itself), so no _internal prefix here.
WEBVIEW_DATAS = [(
    os.path.join(_WEBVIEW_PLUGIN_DIR, _WEBVIEW_BACKEND),
    os.path.join("PySide6", "plugins", "webview"),
)]

block_cipher = None

a = Analysis(
    [os.path.join(REPO, "appmode", "__main__.py")],
    pathex=[REPO],
    binaries=[],
    datas=WEBVIEW_DATAS,
    hiddenimports=[
        # Our own modules, explicit so no entry point misses one.
        "appmode",
        "appmode.shell",
        "appmode.connect",
        "appmode.restart",
        "api",
        "proxy",
        "db",
        "devpanel",
        "devpanel.panel",
        "devpanel.map",
        "devpanel.states",
        # Uvicorn picks these at runtime through importlib, so the
        # static scan cannot see them. HTTP serves plain forms, so
        # h11 is the path; the auto selectors stay listed so a
        # missing optional library falls back instead of failing.
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.loops.asyncio",
        "uvicorn.loops.auto",
        # Starlette parses HTML forms through multipart lazily, so
        # the static scan cannot see it. Without this the panel
        # buttons 500 on every submit.
        "multipart",
        # YAML for the Continue config path. Pure Python is fine.
        "yaml",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test and bench code never ships in the consumer app.
        "tests",
        "pytest",
        "hypothesis",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Iris",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console window, ever. This is the hard requirement: the
    # consumer app is a window, not a terminal.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Iris",
)
