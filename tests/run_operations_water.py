"""Consolidated entry point for operations, lobby, government funding, and water detection tests.
Run from project root: python -m unittest tests.run_operations_water -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

def load_tests(loader, standard_tests, pattern):
    """Load test_lobby_and_government_funding plus any other operations/water tests."""
    from test_lobby_and_government_funding import (
        TestLobbyInflationAndMinimumCost,
        TestLobbySuccessChance,
        TestLobbyOptions,
        TestTryLobby,
        TestDistributeGovernmentFunding,
        TestWaterCheckDeadline,
        TestWaterDetectionSouthFerryGovernorsIsland,
    )
    suite = unittest.TestSuite()
    for cls in (
        TestLobbyInflation,
        TestLobbySuccessChance,
        TestLobbyOptions,
        TestTryLobby,
        TestDistributeGovernmentFunding,
        TestWaterCheckDeadline,
        TestWaterDetectionSouthFerryGovernorsIsland,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite
