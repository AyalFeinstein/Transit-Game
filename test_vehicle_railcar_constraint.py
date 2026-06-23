import unittest
from vehicle import Locomotive, Railcar, Trainset
from game_resource import Coal, Electricity
from vehicle_presets import get_vehicle_registry


class TestVehicleRailcarConstraint(unittest.TestCase):
    """Test that EMUs, DMUs, streetcars, and subway cars cannot have railcars"""
    
    def test_emu_cannot_have_railcars(self):
        """Test that EMU locomotives cannot have railcars"""
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        emu_locomotives = [l for l in locomotives if 'EMU' in l.name]
        self.assertGreater(len(emu_locomotives), 0, "Should find EMU locomotives")
        
        for emu in emu_locomotives:
            self.assertFalse(emu.can_have_railcars, f"{emu.name} should not be able to have railcars")
            
            # Try to create trainset with railcar - should raise error
            railcar = Railcar("Test Car", 0, 50, 10)
            with self.assertRaises(ValueError, msg=f"{emu.name} should raise ValueError when trying to add railcars"):
                Trainset(locomotive=emu, cars=[railcar])
            
            # Create trainset without railcars - should work
            trainset = Trainset(locomotive=emu, cars=[])
            self.assertEqual(len(trainset.cars), 0)
            self.assertEqual(trainset.capacity, emu.capacity)
    
    def test_dmu_cannot_have_railcars(self):
        """Test that DMU locomotives cannot have railcars"""
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        dmu_locomotives = [l for l in locomotives if 'Multiple Unit' in l.name or 'DMU' in l.name]
        self.assertGreater(len(dmu_locomotives), 0, "Should find DMU locomotives")
        
        for dmu in dmu_locomotives:
            self.assertFalse(dmu.can_have_railcars, f"{dmu.name} should not be able to have railcars")
    
    def test_streetcars_cannot_have_railcars(self):
        """Test that streetcar locomotives cannot have railcars"""
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        streetcars = [l for l in locomotives if 'Streetcar' in l.name or 'streetcar' in l.name.lower()]
        self.assertGreater(len(streetcars), 0, "Should find streetcar locomotives")
        
        for streetcar in streetcars:
            self.assertFalse(streetcar.can_have_railcars, f"{streetcar.name} should not be able to have railcars")
    
    def test_subway_cars_cannot_have_railcars(self):
        """Test that subway car locomotives cannot have railcars"""
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        subways = [l for l in locomotives if 'Subway' in l.name or 'subway' in l.name.lower()]
        self.assertGreater(len(subways), 0, "Should find subway car locomotives")
        
        for subway in subways:
            self.assertFalse(subway.can_have_railcars, f"{subway.name} should not be able to have railcars")
    
    def test_regular_locomotives_can_have_railcars(self):
        """Test that regular locomotives can have railcars"""
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        regular_locomotives = [l for l in locomotives if 
                              'EMU' not in l.name and 
                              'Multiple Unit' not in l.name and 
                              'DMU' not in l.name and
                              'Streetcar' not in l.name and 
                              'Subway' not in l.name and
                              'Railcar' not in l.name and
                              'Interurban' not in l.name and
                              l.can_have_railcars]
        
        self.assertGreater(len(regular_locomotives), 0, "Should find regular locomotives")
        
        for loco in regular_locomotives:
            self.assertTrue(loco.can_have_railcars, f"{loco.name} should be able to have railcars")
            
            # Create trainset with railcar - should work
            railcar = Railcar("Test Car", 0, 50, 10)
            trainset = Trainset(locomotive=loco, cars=[railcar])
            self.assertEqual(len(trainset.cars), 1)
    
    def test_trainset_add_railcar_returns_false_for_self_contained(self):
        """Test that add_railcar returns False for self-contained units"""
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        emu = next((l for l in locomotives if 'EMU' in l.name), None)
        if emu:
            trainset = Trainset(locomotive=emu, cars=[])
            railcar = Railcar("Test Car", 0, 50, 10)
            result = trainset.add_railcar(railcar)
            self.assertFalse(result, "add_railcar should return False for EMU")
            self.assertEqual(len(trainset.cars), 0, "EMU should still have no cars")
    
    def test_ui_create_trainset_validates_constraint(self):
        """Test that UI.create_trainset validates the constraint"""
        from operations import Operations
        
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        registry = get_vehicle_registry()
        locomotives = registry.get_all_locomotives()
        
        emu = next((l for l in locomotives if 'EMU' in l.name), None)
        regular_loco = next((l for l in locomotives if l.can_have_railcars), None)
        
        if emu:
            railcar = Railcar("Test Car", 0, 50, 10)
            with self.assertRaises(ValueError):
                ui.create_trainset(emu, [railcar])
            
            # Should work without railcars
            trainset = ui.create_trainset(emu, [])
            self.assertIsNotNone(trainset)
        
        if regular_loco:
            railcar = Railcar("Test Car", 0, 50, 10)
            trainset = ui.create_trainset(regular_loco, [railcar])
            self.assertIsNotNone(trainset)
            self.assertEqual(len(trainset.cars), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

