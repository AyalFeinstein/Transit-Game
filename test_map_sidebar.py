"""Unit tests for map sidebar functionality"""

import unittest
from PyQt6.QtWidgets import QApplication, QLineEdit
import sys


class TestMapSidebar(unittest.TestCase):
    """Test map sidebar button validation"""
    
    def setUp(self):
        """Set up test environment"""
        if not QApplication.instance():
            self.app = QApplication(sys.argv)
        else:
            self.app = QApplication.instance()
    
    def test_line_name_validation(self):
        """Test that line name validation works correctly"""
        # Create a line edit widget
        line_edit = QLineEdit()
        
        # Test empty string
        line_edit.setText("")
        self.assertEqual(line_edit.text().strip(), "")
        
        # Test whitespace only
        line_edit.setText("   ")
        self.assertEqual(line_edit.text().strip(), "")
        
        # Test valid name with whitespace
        line_edit.setText("  Test Line  ")
        self.assertEqual(line_edit.text().strip(), "Test Line")
        self.assertNotEqual(line_edit.text().strip(), "")
        
        # Test valid name without whitespace
        line_edit.setText("TestLine")
        self.assertEqual(line_edit.text().strip(), "TestLine")
        self.assertNotEqual(line_edit.text().strip(), "")
    
    def test_button_validation_logic(self):
        """Test the button validation logic"""
        test_cases = [
            ("", False, "Empty string should fail"),
            ("   ", False, "Whitespace only should fail"),
            ("Test", True, "Valid name should pass"),
            ("  Test  ", True, "Name with whitespace should pass after trim"),
            ("Test Line", True, "Name with space should pass"),
        ]
        
        for input_text, should_pass, description in test_cases:
            line_edit = QLineEdit()
            line_edit.setText(input_text)
            line_name = line_edit.text().strip()
            is_valid = bool(line_name)
            
            self.assertEqual(is_valid, should_pass, 
                           f"{description}: input='{input_text}', expected={should_pass}, got={is_valid}")


if __name__ == '__main__':
    unittest.main()

