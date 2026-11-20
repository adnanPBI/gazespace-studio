"""Windowed distribution entrypoint with an actionable startup error log."""
from __future__ import annotations

from datetime import datetime, timezone
from multiprocessing import freeze_support
import os
from pathlib import Path
import sys
import traceback


def smoke_test(receipt_path: str) -> int:
    """Initialize and paint the real bundled Qt UI without selecting a camera."""
    import json
    import platform
    from tempfile import TemporaryDirectory

    from PySide6.QtWidgets import QApplication
    from gazespace import __version__
    from gazespace.ui.main_window import MainWindow
    from gazespace.ui.fonts import configure_app_fonts, verify_font_rendering

    path = Path(receipt_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    frozen = bool(getattr(sys, 'frozen', False))
    bundled_guides_present = None
    if frozen:
        app_directory = Path(sys.executable).resolve().parent
        bundled_guides_present = all((app_directory / item).is_file() for item in ('README.md', 'docs/USER_GUIDE.md'))
        if not bundled_guides_present:
            raise RuntimeError('Packaged Studio is missing README.md or docs/USER_GUIDE.md beside its executable')
    app = QApplication.instance() or QApplication(sys.argv[:1])
    configure_app_fonts(app)
    app.setApplicationName('GazeSpace Studio packaging verification')
    errors: list[str] = []
    with TemporaryDirectory(prefix='gazespace_studio_smoke_') as workspace:
        window = MainWindow(workspace=workspace, interactive_errors=False)
        window.error_reported.connect(errors.append)
        window.show()
        pages_painted = 0
        try:
            app.processEvents()
            font_verification = verify_font_rendering(
                window.nav_buttons[0].font(), output_png=path.with_name(path.stem + '_font_probe.png'))
            for index in range(window.pages.count()):
                window.pages.setCurrentIndex(index)
                app.processEvents()
                if window.grab().isNull():
                    raise RuntimeError(f'Qt page {index} failed to paint')
                pages_painted += 1
            window.pages.setCurrentIndex(0)
            app.processEvents()
            if errors:
                raise RuntimeError('; '.join(errors))
            image_path = path.with_suffix('.png')
            if not window.grab().save(str(image_path)):
                raise RuntimeError('Unable to write the native Qt smoke screenshot')
        finally:
            window.close()
            app.processEvents()
        if window.isVisible():
            raise RuntimeError('Studio did not close cleanly after the packaging smoke check')
    report = {
        'status': 'passed',
        'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'version': __version__,
        'platform': platform.platform(),
        'frozen_executable': frozen,
        'bundled_guides_present': bundled_guides_present,
        'qt_platform': app.platformName(),
        'native_ui_pages_painted': pages_painted,
        'font_verification': font_verification,
        'screenshot': image_path.name,
        'camera_opened': False,
        'interactive_windows_monitor_acceptance': False,
    }
    path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0


def run() -> int:
    # pythonw/PyInstaller windowed mode has no console streams. Native/model
    # libraries may still write log messages, so supply a writable sink.
    # Windowed PyInstaller may have no console; preserve errors in user logs.
    if sys.stdout is None or sys.stderr is None:
        log_dir = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'GazeSpace' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        if sys.stdout is None:
            sys.stdout = (log_dir / 'studio_stdout.log').open('a', encoding='utf-8')
        if sys.stderr is None:
            sys.stderr = (log_dir / 'studio_stderr.log').open('a', encoding='utf-8')
    try:
        if '--smoke-test' in sys.argv[1:]:
            import argparse
            parser = argparse.ArgumentParser(description='Verify the packaged native Studio UI')
            parser.add_argument('--smoke-test', required=True, metavar='RECEIPT_JSON')
            args = parser.parse_args()
            return smoke_test(args.smoke_test)
        from gazespace.desktop import main
        return int(main() or 0)
    except Exception as exc:
        folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'GazeSpace' / 'logs'
        path = folder / 'startup_error.log'
        detail = traceback.format_exc()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write('\n' + datetime.now(timezone.utc).isoformat() + '\n' + detail)
            message = f'GazeSpace could not start:\n{type(exc).__name__}: {exc}\n\nDetails: {path}'
        except OSError:
            message = f'GazeSpace could not start:\n{type(exc).__name__}: {exc}\n\nRun GazeSpaceCLI doctor for dependency information.'
        if sys.platform == 'win32' and '--smoke-test' not in sys.argv[1:]:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'GazeSpace startup error', 0x10)
        else:
            print(message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    freeze_support()
    raise SystemExit(run())
