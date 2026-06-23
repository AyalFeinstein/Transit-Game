import sys
sys.path.insert(0, '/home/ayal/PycharmProjects/TransitGame')

from operations import Operations
from game import Game
from player import Player
from map import Map

def test_completed_jobs_persistence():
    ui = Operations()
    ui.start_game(["Player1", "Player2"], 50, 50, 10000, 0, 0)
    
    player1 = ui.get_players()[0]
    
    initial_completed = len(ui.get_completed_jobs())
    print(f"Initial completed jobs: {initial_completed}")
    
    points = [(10, 10), (15, 15), (20, 20)]
    result = ui.create_line_complete("TestLine", points, player1)
    print(f"Line created, build_time: {result.get('build_time')}")
    
    build_queue = ui.get_build_queue()
    print(f"Build queue length: {len(build_queue)}")
    
    if build_queue:
        build = build_queue[0]
        build['work_remaining'] = 1.0
        build['assigned_workers'] = 10
        
        ui.process_build_queue()
        
        after_complete = len(ui.get_completed_jobs())
        print(f"Completed jobs after processing: {after_complete}")
        
        assert after_complete > initial_completed, "Completed jobs should increase"
        
        ui.process_build_queue()
        
        after_second = len(ui.get_completed_jobs())
        print(f"Completed jobs after second processing: {after_second}")
        
        assert after_second == after_complete, f"Completed jobs should persist, got {after_second} expected {after_complete}"
    
    print("✓ Completed jobs persistence test passed")

def test_line_cost_off_preset():
    ui = Operations()
    ui.start_game(["Player1"], 50, 50, 10000, 0, 0)
    
    game_map = ui.get_map()
    game_map.use_osm = False
    
    points = [(10, 10), (15, 15)]
    cost_info = ui.calculate_line_cost(points)
    
    print(f"Line cost for {points}: ${cost_info['cost']}")
    assert cost_info['cost'] > 0, "Cost should be positive"
    
    print("✓ Line cost calculation test passed")

def test_extend_line_button_exists():
    from PyQt6.QtWidgets import QApplication
    import qt
    
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    
    window = qt.MainWindow()
    window.operations = Operations()
    window.operations.start_game(["Player1"], 50, 50, 10000, 0, 0)
    
    player = window.operations.get_players()[0]
    player_id = window.operations.get_player_id(player)
    
    window.player_tabs_dict = {}
    window.current_player_index = 0
    
    tabs = window._create_player_tabs(player)
    
    extend_btn = tabs.get('extend_line_btn')
    assert extend_btn is not None, "Extend line button should exist"
    assert extend_btn.text() == "Extend Line", "Button should have correct text"
    
    print("✓ Extend line button exists test passed")

if __name__ == "__main__":
    test_completed_jobs_persistence()
    test_line_cost_off_preset()
    test_extend_line_button_exists()
    print("\nAll tests passed!")

