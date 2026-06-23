#!/usr/bin/env python3
"""Main entry point for networked Transit Game (alias for main.py)"""
import sys
from PyQt6.QtWidgets import QApplication, QDialog
from qt_networked import NetworkedMainWindow, GameModeDialog, MainWindow

def main():
    app = QApplication(sys.argv)
    
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

