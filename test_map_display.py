"""Unit tests for map display functionality"""

import unittest
import sys
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton
from PyQt6.QtCore import Qt

# Ensure QApplication exists
if not QApplication.instance():
    app = QApplication(sys.argv)


class TestMapDisplay(unittest.TestCase):
    """Test map display and layout structure"""
    
    def setUp(self):
        """Set up test environment"""
        if not QApplication.instance():
            self.app = QApplication(sys.argv)
        else:
            self.app = QApplication.instance()
    
    def test_layout_structure(self):
        """Test that the layout structure is correct"""
        # Create main layout (horizontal)
        main_layout = QHBoxLayout()
        
        # Create sidebar widget (fixed width)
        sidebar = QWidget()
        sidebar.setFixedWidth(250)
        main_layout.addWidget(sidebar, 0)  # Stretch factor 0
        
        # Create map layout (vertical)
        map_layout = QVBoxLayout()
        main_layout.addLayout(map_layout, 1)  # Stretch factor 1
        
        # Add refresh button
        refresh_btn = QPushButton("Refresh Map")
        map_layout.addWidget(refresh_btn)
        
        # Verify button is in layout
        self.assertEqual(map_layout.count(), 1, "Refresh button should be in map_layout")
        self.assertEqual(map_layout.itemAt(0).widget(), refresh_btn, "First item should be refresh button")
        
        # Verify sidebar is in main layout
        self.assertEqual(main_layout.count(), 2, "Main layout should have sidebar and map_layout")
        self.assertEqual(main_layout.itemAt(0).widget(), sidebar, "First item should be sidebar")
    
    def test_widget_visibility(self):
        """Test that widgets are visible"""
        sidebar = QWidget()
        sidebar.setVisible(True)
        sidebar.setEnabled(True)
        
        refresh_btn = QPushButton("Refresh Map")
        refresh_btn.setVisible(True)
        refresh_btn.setEnabled(True)
        
        self.assertTrue(sidebar.isVisible(), "Sidebar should be visible")
        self.assertTrue(refresh_btn.isVisible(), "Refresh button should be visible")
        self.assertTrue(sidebar.isEnabled(), "Sidebar should be enabled")
        self.assertTrue(refresh_btn.isEnabled(), "Refresh button should be enabled")
    
    def test_layout_order(self):
        """Test that sidebar comes before map in layout"""
        main_layout = QHBoxLayout()
        
        sidebar = QWidget()
        sidebar.setFixedWidth(250)
        main_layout.addWidget(sidebar, 0)
        
        map_layout = QVBoxLayout()
        main_layout.addLayout(map_layout, 1)
        
        # Verify order
        self.assertEqual(main_layout.itemAt(0).widget(), sidebar, "Sidebar should be first")
        self.assertEqual(main_layout.itemAt(1).layout(), map_layout, "Map layout should be second")
    
    def test_widget_sizing(self):
        """Test widget sizing"""
        sidebar = QWidget()
        sidebar.setFixedWidth(250)
        
        self.assertEqual(sidebar.width(), 250, "Sidebar should have fixed width of 250")
        
        # Map layout should expand
        main_layout = QHBoxLayout()
        main_layout.addWidget(sidebar, 0)
        map_layout = QVBoxLayout()
        main_layout.addLayout(map_layout, 1)
        
        # Verify stretch factors
        self.assertEqual(main_layout.stretch(0), 0, "Sidebar should have stretch factor 0")
        self.assertEqual(main_layout.stretch(1), 1, "Map layout should have stretch factor 1")


if __name__ == '__main__':
    unittest.main()

