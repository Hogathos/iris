"""App mode entry point: python -m appmode starts the app.

One window on the loopback panel. The proxy and the panel server run
inside the app process and stop with the window. A second start
refuses in plain words and starts nothing.

Loopback only. Metadata only. No bodies, no keys. This module never
sets QT_QPA_PLATFORM: a real window needs a real display.
"""
from __future__ import annotations

import argparse
import sys

from appmode.shell import (
    DEFAULT_PANEL_PORT, DEFAULT_PROXY_PORT, AppController, IrisWindow)


def build_parser() -> argparse.ArgumentParser:
    """Command line for the app: ports, journal, instance name."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-port", type=int,
                        default=DEFAULT_PANEL_PORT)
    parser.add_argument("--proxy-port", type=int,
                        default=DEFAULT_PROXY_PORT)
    parser.add_argument("--db", default=None)
    parser.add_argument("--instance-name", default="iris-app-mode")
    return parser


def main(argv=None) -> int:
    """Start the controller and the window, return the exit code."""
    from PySide6.QtWidgets import QApplication

    args = build_parser().parse_args(argv)
    app = QApplication(sys.argv)
    controller = AppController(
        db_path=args.db, panel_port=args.panel_port,
        proxy_port=args.proxy_port, instance_name=args.instance_name)
    if not controller.start():
        if controller.already_running:
            print("Iris is already running on this machine.")
        else:
            print(f"Iris could not start: {controller.start_error}")
        return 1
    window = IrisWindow(controller)
    window.show()
    code = app.exec()
    controller.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
