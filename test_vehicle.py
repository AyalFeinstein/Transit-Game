"""Consolidated tests for vehicle."""
import unittest

import os
import tempfile
import unittest
from vehicle_presets import get_vehicle_registry, VehicleRegistry, write_vehicles_md
from vehicle import Locomotive, Railcar


class TestVehicleRegistry(unittest.TestCase):
    def test_registry_discovers_all_locomotives(self):
        """Test that registry discovers all locomotives from vehicle_presets"""
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        self.assertGreater(len(locomotives), 0, "Should discover at least one locomotive")
        
        for loco in locomotives:
            self.assertIsInstance(loco, Locomotive, f"{loco} should be a Locomotive")
            self.assertIsNotNone(loco.name, "Locomotive should have a name")
    
    def test_registry_discovers_all_railcars(self):
        """Test that registry discovers all railcars from vehicle_presets"""
        registry = get_vehicle_registry()
        railcars = registry.get_all_railcars()
        
        self.assertGreater(len(railcars), 0, "Should discover at least one railcar")
        
        for car in railcars:
            self.assertIsInstance(car, Railcar, f"{car} should be a Railcar")
    
    def test_get_locomotives_by_year(self):
        """Test filtering locomotives by year"""
        registry = get_vehicle_registry()
        
        all_locomotives = registry.get_locomotives_by_year()
        self.assertGreater(len(all_locomotives), 0)
        
        for loco, year in all_locomotives:
            self.assertIsInstance(loco, Locomotive)
            self.assertIsInstance(year, int)
            self.assertGreaterEqual(year, 1800, "Locomotive year should be reasonable")
        
        early_locomotives = registry.get_locomotives_by_year(1850)
        self.assertGreater(len(early_locomotives), 0)
        for loco, year in early_locomotives:
            self.assertLessEqual(year, 1850)
    
    def test_get_railcars_by_year(self):
        """Test filtering railcars by year"""
        registry = get_vehicle_registry()
        
        all_railcars = registry.get_railcars_by_year()
        self.assertGreater(len(all_railcars), 0)
        
        for car, year in all_railcars:
            self.assertIsInstance(car, Railcar)
            self.assertIsInstance(year, int)
        
        early_railcars = registry.get_railcars_by_year(1850)
        self.assertGreater(len(early_railcars), 0)
        for car, year in early_railcars:
            self.assertLessEqual(year, 1850)
    
    def test_get_earliest_locomotive(self):
        """Test getting earliest locomotive"""
        registry = get_vehicle_registry()
        earliest = registry.get_earliest_locomotive()
        
        self.assertIsNotNone(earliest)
        self.assertIsInstance(earliest, Locomotive)
        
        all_locomotives = registry.get_locomotives_by_year()
        earliest_year = min(year for _, year in all_locomotives)
        self.assertEqual(earliest.year_introduced, earliest_year)
    
    def test_get_earliest_railcar(self):
        """Test getting earliest railcar"""
        registry = get_vehicle_registry()
        earliest = registry.get_earliest_railcar()
        
        self.assertIsNotNone(earliest)
        self.assertIsInstance(earliest, Railcar)
        
        all_railcars = registry.get_railcars_by_year()
        earliest_year = min(year for _, year in all_railcars)
        self.assertEqual(earliest.year_introduced, earliest_year)
    
    def test_registry_singleton(self):
        """Test that get_vehicle_registry returns the same instance"""
        registry1 = get_vehicle_registry()
        registry2 = get_vehicle_registry()
        
        self.assertIs(registry1, registry2, "Should return the same registry instance")
    
    def test_all_vehicles_have_year_introduced(self):
        """Test that all vehicles have year_introduced attribute"""
        registry = get_vehicle_registry()
        
        locomotives = registry.get_locomotives_by_year()
        for loco, year in locomotives:
            self.assertIsNotNone(year, f"{loco.name} should have year_introduced")
            self.assertIsInstance(year, int)
        
        railcars = registry.get_railcars_by_year()
        for car, year in railcars:
            self.assertIsNotNone(year, f"{car.name} should have year_introduced")
            self.assertIsInstance(year, int)


class TestUIVehicleUsage(unittest.TestCase):
    """Test that UI uses vehicle registry correctly"""
    
    def test_generate_players_uses_registry(self):
        """Test that generate_players uses registry to get earliest vehicles that can have railcars"""
        from operations import generate_players
        from vehicle_presets import get_vehicle_registry
        
        registry = get_vehicle_registry()
        # generate_players uses earliest locomotive that can_have_railcars (skips horse carriage, etc.)
        all_locomotives = registry.get_locomotives_by_year()
        expected_loco = None
        for loco, year in all_locomotives:
            if hasattr(loco, 'can_have_railcars') and loco.can_have_railcars:
                expected_loco = loco
                break
        earliest_railcar = registry.get_earliest_railcar()
        
        players = generate_players(["Player1", "Player2"], 10000)
        
        self.assertEqual(len(players), 2)
        for player in players:
            self.assertEqual(len(player.trainsets), 3)
            for trainset in player.trainsets:
                self.assertEqual(trainset.locomotive.year_introduced, expected_loco.year_introduced,
                                 "Should use earliest locomotive that can have railcars from registry")
                self.assertEqual(len(trainset.cars), 1)
                self.assertEqual(trainset.cars[0].capacity, earliest_railcar.capacity)
    
    def test_initialize_marketplace_uses_registry(self):
        """Test that initialize_marketplace_trainsets uses registry"""
        from operations import initialize_marketplace_trainsets
        from marketplace import Marketplace
        from vehicle_presets import get_vehicle_registry
        
        registry = get_vehicle_registry()
        marketplace = Marketplace()
        
        initialize_marketplace_trainsets(marketplace, num_players=2)
        
        available_vehicles = marketplace.get_available_vehicles()
        self.assertGreater(len(available_vehicles), 0)
        
        earliest_loco = registry.get_earliest_locomotive()
        found_loco = False
        for sale_item in available_vehicles:
            vehicle = sale_item.get_item()
            if hasattr(vehicle, 'locomotive') and vehicle.locomotive:
                if vehicle.locomotive.name == earliest_loco.name:
                    found_loco = True
                    break
            elif hasattr(vehicle, 'name') and vehicle.name == earliest_loco.name:
                found_loco = True
                break
        
        self.assertTrue(found_loco, "Should find earliest locomotive in marketplace")


class TestMarketplaceVehicleUsage(unittest.TestCase):
    """Test that Marketplace uses vehicle registry correctly"""
    
    def test_generate_trainsets_uses_registry(self):
        """Test that generate_trainsets_based_on_discoveries uses registry"""
        from marketplace import Marketplace
        from vehicle_presets import get_vehicle_registry
        
        registry = get_vehicle_registry()
        marketplace = Marketplace()
        
        all_locomotives = registry.get_locomotives_by_year(1900)
        self.assertGreater(len(all_locomotives), 0)
        
        marketplace.generate_trainsets_based_on_discoveries(1900)
        
        available_vehicles = marketplace.get_available_vehicles()
        self.assertGreaterEqual(len(available_vehicles), 0)
    
    def test_add_trainsets_for_new_technology_uses_registry(self):
        """Test that add_trainsets_for_new_technology uses registry"""
        from marketplace import Marketplace
        from vehicle_presets import get_vehicle_registry
        
        registry = get_vehicle_registry()
        marketplace = Marketplace()
        
        all_locomotives = registry.get_locomotives_by_year()
        if all_locomotives:
            first_loco, first_year = all_locomotives[0]
            newly_researched = [(first_loco.name, first_year)]
            
            marketplace.add_trainsets_for_new_technology(first_year, newly_researched)
            
            available_vehicles = marketplace.get_available_vehicles()
            self.assertGreaterEqual(len(available_vehicles), 0)


class TestGameVehicleUsage(unittest.TestCase):
    """Test that Game uses vehicle registry correctly"""
    
    def test_get_newly_available_vehicles_uses_registry(self):
        """Test that get_newly_available_vehicles uses registry"""
        from game import Game
        from player import Player
        from map import Map
        from timeline import Timeline
        from marketplace import Marketplace
        from vehicle_presets import get_vehicle_registry
        
        registry = get_vehicle_registry()
        all_locomotives = registry.get_locomotives_by_year()
        
        players = [Player(0, money=10000, name="Player1")]
        game_map = Map(width=10, height=10)
        timeline = Timeline(initial_year=1830, end_year=2050)
        marketplace = Marketplace()
        
        game = Game(players=players, game_map=game_map, timeline=timeline, marketplace=marketplace)
        
        newly_available = game.get_newly_available_vehicles(1829, 1835)
        
        self.assertGreater(len(newly_available), 0)
        for name, year in newly_available:
            self.assertIsInstance(name, str)
            self.assertIsInstance(year, int)
            self.assertGreater(year, 1829)
            self.assertLessEqual(year, 1835)


class TestWriteVehiclesMd(unittest.TestCase):
    """Test write_vehicles_md uses vehicle __repr__ to generate VEHICLES.md."""

    def test_write_vehicles_md_creates_file_with_repr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vehicles_out.md")
            write_vehicles_md(path)
            self.assertTrue(os.path.isfile(path))
            content = open(path, encoding="utf-8").read()
            self.assertIn("# Vehicle Details", content)
            self.assertIn("## Locomotives", content)
            self.assertIn("## Railcars", content)
            registry = get_vehicle_registry()
            locos = registry.get_locomotives_by_year()
            if locos:
                first_loco = locos[0][0]
                self.assertIn(repr(first_loco), content)

if __name__ == '__main__':
    unittest.main()
