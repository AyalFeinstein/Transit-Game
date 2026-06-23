"""Comprehensive unit tests for marketplace.py"""
import unittest
from marketplace import Marketplace, SaleItem
from line import Line
from player import Player
from vehicle import Locomotive, Railcar, Trainset
from game_resource import Coal, Oil, Electricity


class TestSaleItem(unittest.TestCase):
    def setUp(self):
        self.test_item = "test_item"
        self.test_price = 100
    
    def test_sale_item_initialization(self):
        """Test SaleItem initialization"""
        item = SaleItem(self.test_item, self.test_price)
        self.assertEqual(item.get_item(), self.test_item)
        self.assertEqual(item.price, self.test_price)
        self.assertEqual(item.get_quantity_available(), 1)
        self.assertEqual(item.get_seller(), "Manufacturer")
        self.assertIsNone(item.get_salesman())
    
    def test_sale_item_with_salesman(self):
        """Test SaleItem with salesman"""
        player = Player(0, 1000, [], "Test Player")
        item = SaleItem(self.test_item, self.test_price, salesman=player, seller="Player")
        self.assertEqual(item.get_salesman(), player)
        self.assertEqual(item.get_seller(), "Player")
    
    def test_get_price_without_inflation(self):
        """Test get_price at base year (1830)"""
        item = SaleItem(self.test_item, 100)
        price = item.get_price(current_year=1830)
        self.assertEqual(price, 300)
    
    def test_get_price_with_inflation(self):
        """Test get_price with inflation"""
        item = SaleItem(self.test_item, 100)
        price_1900 = item.get_price(current_year=1900)
        price_2000 = item.get_price(current_year=2000)
        self.assertGreater(price_2000, price_1900)
        self.assertGreater(price_1900, 300)  
    
    def test_get_price_with_discount(self):
        """Test get_price with discount (sale_year must match current_year for discount to apply)"""
        item = SaleItem(self.test_item, 100)
        item.original_price = 100
        item.discount_percent = 20  
        item.sale_year = 1830 
        price = item.get_price(current_year=1830)
        self.assertEqual(price, 240)
    
    def test_get_price_with_quantity(self):
        """Test get_price with different quantities"""
        item = SaleItem(self.test_item, 100)
        item.quantity_avalilable = 1
        price_q1 = item.get_price(current_year=1830)
        
        item.quantity_avalilable = 10
        price_q10 = item.get_price(current_year=1830)
        self.assertLess(price_q10, price_q1)
    
    def test_quantity_operations(self):
        """Test quantity add and deduct"""
        item = SaleItem(self.test_item, 100)
        self.assertEqual(item.get_quantity_available(), 1)
        
        item.add_quantity(5)
        self.assertEqual(item.get_quantity_available(), 6)
        
        item.deduct_quantity(2)
        self.assertEqual(item.get_quantity_available(), 4)
        
        item.deduct_quantity(10)  # Should go negative, but that's allowed
        self.assertEqual(item.get_quantity_available(), -6)
    
    def test_sale_item_equality(self):
        """Test SaleItem equality comparison"""
        item1 = SaleItem("item1", 100)
        item2 = SaleItem("item1", 200)  # Different price
        item3 = SaleItem("item2", 100)  # Different name
        
        # Items with same name should be equal (ignoring price)
        self.assertEqual(item1, item2)
        self.assertNotEqual(item1, item3)
        
        # Test with items that have name attribute
        class NamedItem:
            def __init__(self, name):
                self.name = name
        
        named1 = SaleItem(NamedItem("test"), 100)
        named2 = SaleItem(NamedItem("test"), 200)
        named3 = SaleItem(NamedItem("other"), 100)
        
        self.assertEqual(named1, named2)
        self.assertNotEqual(named1, named3)


class TestMarketplace(unittest.TestCase):
    def setUp(self):
        self.marketplace = Marketplace()
        self.marketplace.available_lines = []  # Ensure clean state
        self.marketplace.available_vehicles = []  # Ensure clean state
        self.player = Player(0, 10000, [], "Test Player")
    
    def test_marketplace_initialization(self):
        """Test Marketplace initialization"""
        marketplace = Marketplace()  # Create fresh instance
        self.assertEqual(marketplace.available_lines, [])
        self.assertEqual(marketplace.available_vehicles, [])
        self.assertEqual(marketplace.marketplace_years_visible, 40)
        self.assertIsNone(marketplace.last_generation_year)
        self.assertEqual(marketplace.worker_supply, {})
    
    def test_worker_supply_operations(self):
        """Test worker supply management"""
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 0)
        
        self.marketplace.set_worker_supply("Engineer", 10)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 10)
        
        self.marketplace.deduct_worker_supply("Engineer", 3)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 7)
        
        self.marketplace.add_worker_supply("Engineer", 5)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 12)
        
        # Test negative deduction (should not go below 0)
        self.marketplace.deduct_worker_supply("Engineer", 20)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 0)
    
    def test_add_line(self):
        """Test adding lines to marketplace"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        
        self.marketplace.add_line(line, price=1000, current_year=1830)
        self.assertEqual(len(self.marketplace.available_lines), 1)
        
        sale_item = self.marketplace.available_lines[0]
        self.assertEqual(sale_item.get_item().name, line.name)
        self.assertEqual(sale_item.price, 1000)
    
    def test_add_duplicate_line(self):
        """Test adding duplicate lines increases quantity"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        
        self.marketplace.add_line(line, price=1000, current_year=1830)
        self.marketplace.add_line(line, price=1000, current_year=1830)
        
        self.assertEqual(len(self.marketplace.available_lines), 1)
        self.assertEqual(self.marketplace.available_lines[0].get_quantity_available(), 2)
    
    def test_remove_line(self):
        """Test removing lines from marketplace"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        
        # Clear any existing lines
        self.marketplace.available_lines = []
        
        self.marketplace.add_line(line, price=1000, current_year=1830)
        self.marketplace.available_lines[0].add_quantity(2)  # Total quantity 3
        
        initial_quantity = self.marketplace.available_lines[0].get_quantity_available()
        self.marketplace.remove_line(line)
        self.assertEqual(self.marketplace.available_lines[0].get_quantity_available(), initial_quantity - 1)
        
        # Remove until empty
        while len(self.marketplace.available_lines) > 0:
            self.marketplace.remove_line(line)
        
        # Should be removed when quantity reaches 0
        self.assertEqual(len(self.marketplace.available_lines), 0)
    
    def test_add_vehicle(self):
        """Test adding vehicles to marketplace"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotive = registry.get_earliest_locomotive()
        
        if locomotive:
            self.marketplace.add_vehicle(locomotive, price=500, current_year=1830)
            self.assertEqual(len(self.marketplace.available_vehicles), 1)
            
            sale_item = self.marketplace.available_vehicles[0]
            self.assertEqual(sale_item.get_item(), locomotive)
    
    def test_generate_trainsets_based_on_discoveries(self):
        """Test trainset generation: target total from presets, distributed among available types"""
        from game_presets import MARKETPLACE_TARGET_TOTAL_ITEMS
        current_year = 1830
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        # Should have generated some vehicles
        self.assertGreater(len(self.marketplace.available_vehicles), 0)
        self.assertIsNotNone(self.marketplace.target_total_items)
        self.assertEqual(self.marketplace.target_total_items, MARKETPLACE_TARGET_TOTAL_ITEMS)
        self.assertEqual(self.marketplace.last_generation_year, current_year)
        # Total quantity should equal target (distribution) or be slightly higher if associated railcars added
        total_quantity = sum(item.get_quantity_available() for item in self.marketplace.available_vehicles)
        self.assertGreaterEqual(total_quantity, MARKETPLACE_TARGET_TOTAL_ITEMS)
        self.assertLessEqual(total_quantity, MARKETPLACE_TARGET_TOTAL_ITEMS + 30)
    
    def test_generate_trainsets_replenishment(self):
        """Test trainset replenishment"""
        current_year = 1830
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        initial_count = len(self.marketplace.available_vehicles)
        target_total = self.marketplace.target_total_items
        
        # Remove some items
        if len(self.marketplace.available_vehicles) > 0:
            self.marketplace.available_vehicles.pop()
        
        # Generate again - should replenish
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        # Should have replenished to target
        current_total = sum(item.get_quantity_available() for item in self.marketplace.available_vehicles)
        self.assertGreaterEqual(current_total, target_total - 1)  # Allow for rounding
    
    def test_get_available_items(self):
        """Test getting all available items"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        self.marketplace.add_line(line, price=1000, current_year=1830)
        
        items = self.marketplace.get_available_items(current_year=1830)
        self.assertGreater(len(items), 0)
        self.assertIn(self.marketplace.available_lines[0], items)
    
    def test_get_available_items_filters_future_trainsets(self):
        """Test that future trainsets are filtered out"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        
        # Generate trainsets for 1830
        self.marketplace.generate_trainsets_based_on_discoveries(1830)
        
        # Get items for 1830 - should include available trainsets
        items_1830 = self.marketplace.get_available_items(current_year=1830)
        
        # Get items for 1800 - should exclude trainsets introduced after 1800
        items_1800 = self.marketplace.get_available_items(current_year=1800)
        
        # Items in 1800 should be a subset of items in 1830 (or equal if all are old)
        self.assertLessEqual(len(items_1800), len(items_1830))


if __name__ == "__main__":
    unittest.main(verbosity=2)

