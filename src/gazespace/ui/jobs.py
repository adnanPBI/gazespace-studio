"""Run calibration/file analysis outside the UI event loop."""
from PySide6.QtCore import QObject, Signal, Slot, QRunnable


class JobSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class FunctionJob(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = JobSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.fn())
        except Exception as exc:
            self.signals.error.emit(f'{type(exc).__name__}: {exc}')
        finally:
            self.signals.finished.emit()
