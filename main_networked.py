#!/usr/bin/env python3
"""Main entry point for networked Railroad Baron (alias for main.py)"""
import sys
from PyQt6.QtWidgets import QApplication, QDialog
from qt_networked import NetworkedMainWindow, GameModeDialog, MainWindow
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
            # Use regular MainWindow for local play
            window = MainWindow()
            window.show()
        elif dialog.choice == 'network':
            # Use NetworkedMainWindow for networked play
            window = NetworkedMainWindow(network_mode=True)
            window.show()
        else:
            sys.exit(0)
    else:
        sys.exit(0)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

