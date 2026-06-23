"""Comprehensive unit tests for player.py - Player 2 focus.
These tests mirror test_player_comprehensive.py but use Player 2 (ID 1).
This ensures all players operate the same way."""
import unittest
from player import Player
from vehicle import Trainset, Locomotive, Railcar
from line import Line


class TestPlayer2(unittest.TestCase):
    def setUp(self):
        # Use Player 2 (ID 1) instead of Player 1 (ID 0)
        self.player = Player(1, 10000, [], "Test Player 2")
    
    def test_player_initialization(self):
        """Test Player initialization"""
        self.assertEqual(self.player.player_id, 1)
        self.assertEqual(self.player.get_money(), 10000)
        self.assertEqual(self.player.get_name(), "Test Player 2")
        self.assertEqual(self.player.get_line_names(), [])
        self.assertEqual(self.player.get_trainsets(), [])
        self.assertFalse(self.player.is_eliminated)
    
    def test_player_initialization_with_defaults(self):
        """Test Player initialization with defaults"""
        player = Player(2)
        self.assertEqual(player.player_id, 2)
        self.assertEqual(player.get_money(), 0)
        self.assertEqual(player.get_name(), "")
        self.assertEqual(player.get_line_names(), [])
        self.assertEqual(player.get_trainsets(), [])
    
    def test_money_operations(self):
        """Test money operations"""
        self.assertEqual(self.player.get_money(), 10000)
        
        self.player.add_money(5000)
        self.assertEqual(self.player.get_money(), 15000)
        
        result = self.player.deduct_money(3000)
        self.assertEqual(result, 12000)
        self.assertEqual(self.player.get_money(), 12000)
    
    def test_acquire_line(self):
        """Test acquiring lines"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        
        self.player.acquire_line(line)
        self.assertIn("Test Line", self.player.get_line_names())
        
        # Acquiring again should not duplicate
        self.player.acquire_line(line)
        self.assertEqual(self.player.get_line_names().count("Test Line"), 1)
    
    def test_remove_line(self):
        """Test removing lines"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        self.player.acquire_line(line)
        
        self.player.remove_line("Test Line")
        self.assertNotIn("Test Line", self.player.get_line_names())
        
        # Removing non-existent line should raise error
        with self.assertRaises(ValueError):
            self.player.remove_line("Non-existent Line")
    
    def test_trainset_operations(self):
        """Test trainset operations"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotive = registry.get_earliest_locomotive()
        railcar = registry.get_earliest_railcar()
        
        if locomotive and railcar:
            cars = [Railcar("Test Car", 0, railcar.capacity, railcar.maitnance_costs)]
            trainset = Trainset(locomotive=locomotive, cars=cars)
            
            self.player.aquire_trainset(trainset)
            self.assertEqual(len(self.player.get_trainsets()), 1)
            self.assertIn(trainset, self.player.get_trainsets())
            
            self.player.remove_trainset(trainset)
            self.assertEqual(len(self.player.get_trainsets()), 0)
            
            # Removing non-existent trainset should raise error
            with self.assertRaises(ValueError):
                self.player.remove_trainset(trainset)
    
    def test_get_trainsets_by_line(self):
        """Test getting trainsets by line assignment"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotive = registry.get_earliest_locomotive()
        railcar = registry.get_earliest_railcar()
        
        if locomotive and railcar:
            cars1 = [Railcar("Car1", 0, railcar.capacity, railcar.maitnance_costs)]
            trainset1 = Trainset(locomotive=locomotive, cars=cars1)
            trainset1.assign_to_line("Line1")
            
            cars2 = [Railcar("Car2", 0, railcar.capacity, railcar.maitnance_costs)]
            trainset2 = Trainset(locomotive=locomotive, cars=cars2)
            trainset2.assign_to_line("Line2")
            
            cars3 = [Railcar("Car3", 0, railcar.capacity, railcar.maitnance_costs)]
            trainset3 = Trainset(locomotive=locomotive, cars=cars3)
            # trainset3 not assigned
            
            self.player.aquire_trainset(trainset1)
            self.player.aquire_trainset(trainset2)
            self.player.aquire_trainset(trainset3)
            
            line1_trainsets = self.player.get_trainsets_by_line("Line1")
            self.assertEqual(len(line1_trainsets), 1)
            self.assertIn(trainset1, line1_trainsets)
            
            unassigned = self.player.get_unassigned_trainsets()
            self.assertEqual(len(unassigned), 1)
            self.assertIn(trainset3, unassigned)
    
    def test_player_elimination(self):
        """Test player elimination"""
        self.assertFalse(self.player.is_eliminated)
        self.assertFalse(self.player.is_player_eliminated())
        
        self.player.eliminate()
        self.assertTrue(self.player.is_eliminated)
        self.assertTrue(self.player.is_player_eliminated())
    
    def test_rename(self):
        """Test renaming player"""
        self.assertEqual(self.player.get_name(), "Test Player 2")
        self.player.rename("New Name")
        self.assertEqual(self.player.get_name(), "New Name")
    
    def test_worker_rentals(self):
        """Test worker rental operations"""
        from worker import ENGINEER, WorkerRental
        
        # Initially no engineers
        self.assertEqual(self.player.get_engineer_count(), 0)
        
        # Rent engineers
        self.player.rent_workers(ENGINEER, 5, current_year=1830)
        self.assertEqual(self.player.get_engineer_count(), 5)
        
        # Rent more engineers
        self.player.rent_workers(ENGINEER, 3, current_year=1830)
        self.assertEqual(self.player.get_engineer_count(), 8)
        
        # Get rental object
        rental = self.player.get_worker_rental(ENGINEER.name)
        self.assertIsNotNone(rental)
        self.assertEqual(rental.quantity, 8)
    
    def test_lawsuit_excuses(self):
        """Test lawsuit excuse operations"""
        from lawsuit import LawsuitExcuse
        
        excuse = LawsuitExcuse("Test excuse", self.player, 1830)
        
        self.assertEqual(len(self.player.get_lawsuit_excuses()), 0)
        
        self.player.add_lawsuit_excuse(excuse)
        self.assertEqual(len(self.player.get_lawsuit_excuses()), 1)
        self.assertIn(excuse, self.player.get_lawsuit_excuses())
        
        self.player.remove_lawsuit_excuse(excuse)
        self.assertEqual(len(self.player.get_lawsuit_excuses()), 0)
    
    def test_get_assigned_trainsets_count(self):
        """Test counting assigned trainsets"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotive = registry.get_earliest_locomotive()
        railcar = registry.get_earliest_railcar()
        
        if locomotive and railcar:
            cars1 = [Railcar("Car1", 0, railcar.capacity, railcar.maitnance_costs)]
            trainset1 = Trainset(locomotive=locomotive, cars=cars1)
            trainset1.assign_to_line("Line1")
            
            cars2 = [Railcar("Car2", 0, railcar.capacity, railcar.maitnance_costs)]
            trainset2 = Trainset(locomotive=locomotive, cars=cars2)
            # trainset2 not assigned
            
            self.player.aquire_trainset(trainset1)
            self.player.aquire_trainset(trainset2)
            
            self.assertEqual(self.player.get_assigned_trainsets_count(), 1)
            
            assigned = self.player.get_assigned_trainsets()
            self.assertEqual(len(assigned), 1)
            self.assertIn(trainset1, assigned)


if __name__ == "__main__":
    unittest.main(verbosity=2)

