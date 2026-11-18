"""Native Windows desktop launcher. Qt is an optional install extra."""
from __future__ import annotations
import os
from pathlib import Path
import sys


def launch(workspace=None, profile=None, demo=False):
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        from .ui.main_window import MainWindow
        from .ui.fonts import configure_app_fonts
    except ImportError as exc:
        raise RuntimeError("Desktop needs python -m pip install 'gazespace[desktop,face,api]'") from exc
    app=QApplication.instance() or QApplication(sys.argv[:1])
    configure_app_fonts(app)
    app.setApplicationName('GazeSpace Studio')
    app.setOrganizationName('GazeSpace')
    window=MainWindow(workspace=workspace,profile=profile)
    window.show()
    if demo:QTimer.singleShot(100,window.open_demo)
    return app.exec()


def main():
    import argparse
    parser=argparse.ArgumentParser(description='GazeSpace native desktop')
    parser.add_argument('--workspace',type=Path)
    parser.add_argument('--profile',type=Path)
    parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    return launch(args.workspace,args.profile,args.demo)


if __name__=='__main__':
    raise SystemExit(main())
