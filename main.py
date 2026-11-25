#!/usr/bin/env python3
"""Main entry point for GoCommuter - uses qt_networked for local and networked play"""
import logging
import os
import sys


def _configure_logging() -> None:
    """Enable console logging; use GOCOMMUTER_LOG_LEVEL=DEBUG (default when --debug)."""
    if '--debug' in sys.argv:
        sys.argv = [a for a in sys.argv if a != '--debug']
        os.environ.setdefault('GOCOMMUTER_LOG_LEVEL', 'DEBUG')
    level_name = os.environ.get(
        'GOCOMMUTER_LOG_LEVEL',
        os.environ.get('TRANSITGAME_LOG_LEVEL', 'INFO'),
    ).upper()
    level = getattr(logging, level_name, logging.INFO)
    if not logging.root.handlers:
        logging.basicConfig(
            level=level,
            format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
            datefmt='%H:%M:%S',
        )
    else:
        logging.root.setLevel(level)
    logging.getLogger('qt_networked').setLevel(level)
    if level <= logging.DEBUG:
        logging.getLogger('operations').setLevel(logging.DEBUG)
        logging.getLogger('ai').setLevel(logging.DEBUG)


_configure_logging()

import crash_log
crash_log.install()  # unexpected errors are logged and shown instead of closing the game

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from qt_networked import GameModeDialog, MainWindow, NetworkedMainWindow
from qt_networked import _install_osm_tile_interceptor, application_window_icon

def main():
    app = QApplication(sys.argv)
    app_icon = application_window_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    _install_osm_tile_interceptor()
    
    # Show game mode selection dialog
    dialog = GameModeDialog()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        if dialog.choice == 'local':
            window = MainWindow()
            window.show()
        elif dialog.choice == 'network':
            window = NetworkedMainWindow(network_mode=True)
            window.show()
        else:
            sys.exit(0)
    else:
        sys.exit(0)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
