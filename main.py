#!/usr/bin/env python3
"""Main entry point for Transit Game - uses qt_networked for local and networked play"""
import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from qt_networked import GameModeDialog, MainWindow, NetworkedMainWindow


def main():
    app = QApplication(sys.argv)
    
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
