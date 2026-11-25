"""Keep the game running after an unexpected error, and record what happened.

PyQt6 aborts the whole application when a Python exception escapes a Qt callback (a button,
a timer, a dialog) unless ``sys.excepthook`` is set. ``install`` sets one that writes the full
traceback to ``gocommuter_errors.log`` (next to this file), shows a short error message and
lets the game carry on. Hard crashes inside Qt itself are written to the same log by
``faulthandler``.
"""
from __future__ import annotations

import datetime
import faulthandler
import logging
import os
import sys
import traceback

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gocommuter_errors.log')

_fault_file = None
_showing_dialog = False


def _write(text: str) -> None:
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(text)
    except OSError:
        pass


def _excepthook(exc_type, exc, tb) -> None:
    global _showing_dialog
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    details = ''.join(traceback.format_exception(exc_type, exc, tb))
    _write(f"\n=== {stamp} unhandled error ===\n{details}")
    logging.getLogger('gocommuter').error("Unhandled error (logged to %s):\n%s", LOG_PATH, details)
    if _showing_dialog:
        return
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is None:
            return
        _showing_dialog = True
        QMessageBox.critical(
            None, "Something went wrong",
            f"An unexpected error occurred, but the game is still running.\n\n{exc_type.__name__}: {exc}\n\n"
            f"Details were saved to:\n{LOG_PATH}",
        )
    except Exception:
        pass
    finally:
        _showing_dialog = False


def install() -> None:
    """Log unhandled errors (and keep running) and native crash stacks to ``LOG_PATH``."""
    global _fault_file
    sys.excepthook = _excepthook
    try:
        _fault_file = open(LOG_PATH, 'a', encoding='utf-8')
        faulthandler.enable(file=_fault_file, all_threads=True)
    except OSError:
        faulthandler.enable()
