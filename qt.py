import sys
import pickle
import os
from pathlib import Path
import warnings

warnings.filterwarnings('ignore', category=DeprecationWarning, message='.*sipPyTypeDict.*')

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtCore import QUrl, QDir
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    QWebEngineView = None
    QUrl = None
    QDir = None

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QTabWidget,
    QLabel,
    QDialog,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QInputDialog,
    QCheckBox,
    QGroupBox,
    QSlider,
    QScrollArea,
    QLineEdit,
    QFrame,
    QListWidget,
    QSizePolicy,
    QTextBrowser,
)
from PyQt6.QtGui import QColor, QPalette, QPainter, QPen, QBrush, QFont, QAction
from PyQt6.QtCore import Qt
from operations import Operations
from game_presets import STARTING_MONEY
from inflation_utils import get_inflation_factor
# Vehicle imports removed - using dynamic registry system instead
from vehicle import Locomotive, Railcar, Trainset, Ferry, Bus

# Maximum time (ms) for line creation and map refresh - prevents long freezes
LINE_CREATION_MAX_MS = 15000
MAP_REFRESH_MAX_MS = 15000

# Save games directory (relative to project root)
SAVEGAMES_DIR = Path(__file__).resolve().parent / "savegames"


class LineCreationWorker(QObject):
    """Runs create_line_complete in a background thread to avoid blocking the UI."""
    finished = pyqtSignal(object, bool, str)  # result, error_occurred, error_message

    def __init__(self, operations, line_name, grid_points, current_player, elevation_levels, is_ferry_line, is_bus_line):
        super().__init__()
        self.operations = operations
        self.line_name = line_name
        self.grid_points = grid_points
        self.current_player = current_player
        self.elevation_levels = elevation_levels
        self.is_ferry_line = is_ferry_line
        self.is_bus_line = is_bus_line

    def run(self):
        result = None
        error_occurred = False
        error_message = ""
        try:
            result = self.operations.create_line_complete(
                self.line_name, self.grid_points, self.current_player, self.elevation_levels,
                is_ferry_line=self.is_ferry_line, is_bus_line=self.is_bus_line
            )
        except Exception as e:
            import traceback
            print(f"Error in line creation worker: {e}")
            traceback.print_exc()
            error_occurred = True
            error_message = str(e)
        self.finished.emit(result, error_occurred, error_message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transit Game")
        self.setGeometry(100, 100, 800, 600)
        central_widget = QWidget()
        central_layout = QVBoxLayout()
        central_widget.setLayout(central_layout)
        self.setCentralWidget(central_widget)
        self.tabs = QTabWidget()
        # Initialize station creation state
        self._station_creation_state = {}
        central_layout.addWidget(self.tabs)
        self.status_bar = None
        self.status_bar_layout = None
        self.status_year_label = None
        self.status_money_labels = {}
        self.status_end_year_btn = None
        self.game_tab = QWidget()
        self.game_layout = QVBoxLayout()
        self.game_tab.setLayout(self.game_layout)
        self.tabs.addTab(self.game_tab, "Game")
        self.info_tab = None
        self.info_layout = None
        self.marketplace_tab = None
        self.marketplace_layout = None
        self.current_player_index = 0
        self.last_lawyer_replenishment_year = None
        self.layout = self.game_layout
        self.player_tabs_dict = {}
        self.operations = None 
        self._setup_menu_bar()
        self.SetupGameTab()
    
    def _setup_menu_bar(self):
        """Create the menu bar with File menu and New Game action"""
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")
        new_game_action = QAction("New Game", self)
        new_game_action.setShortcut("Ctrl+N")
        new_game_action.triggered.connect(self.on_new_game_clicked)
        file_menu.addAction(new_game_action)
        file_menu.addSeparator()
        load_game_action = QAction("Load Game", self)
        load_game_action.setShortcut("Ctrl+O")
        load_game_action.triggered.connect(self.on_load_game_clicked)
        file_menu.addAction(load_game_action)
        
        # Add Save Game action (enabled when game is running)
        self.save_game_action = QAction("Save Game", self)
        self.save_game_action.setShortcut("Ctrl+S")
        self.save_game_action.triggered.connect(self.on_save_game_clicked)
        self.save_game_action.setEnabled(False)  # Enabled when game starts
        file_menu.addAction(self.save_game_action)
        file_menu.addSeparator()
        
        # Game menu with common operations
        game_menu = menu_bar.addMenu("Game")
        self.advance_year_action = QAction("Advance Year", self)
        self.advance_year_action.setShortcut("Ctrl+Y")
        self.advance_year_action.triggered.connect(self._menu_advance_year)
        self.advance_year_action.setEnabled(False)
        game_menu.addAction(self.advance_year_action)
        self.refresh_displays_action = QAction("Refresh Displays", self)
        self.refresh_displays_action.triggered.connect(self._menu_refresh_displays)
        self.refresh_displays_action.setEnabled(False)
        game_menu.addAction(self.refresh_displays_action)
    
    def _menu_advance_year(self):
        """Menu action: Advance year (same as End Year button)"""
        if self.operations:
            self.advance_year_button_clicked()
    
    def _menu_refresh_displays(self):
        """Menu action: Refresh all displays"""
        if self.operations:
            self.refresh_all_displays()
    
    def on_new_game_clicked(self):
        """Handle New Game menu action - reset to game setup screen"""
        if self.operations is not None:
            reply = QMessageBox.question(
                self,
                "New Game",
                "Are you sure you want to start a new game? This will end the current game.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        self.operations = None
        while self.tabs.count() > 1:
            self.tabs.removeTab(1)
        self.player_tabs_dict = {}
        self.current_player_index = 0
        if self.status_bar:
            self.status_bar.clearMessage()
            if self.status_bar_layout:
                while self.status_bar_layout.count():
                    item = self.status_bar_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
        
        self.status_year_label = None
        self.status_money_labels = {}
        self.status_end_year_btn = None
        self.tabs.setCurrentIndex(0)
        self.last_lawyer_replenishment_year = None
        self._station_creation_state = {}
        # Disable game menu actions when no game
        if hasattr(self, 'advance_year_action'):
            self.advance_year_action.setEnabled(False)
        if hasattr(self, 'refresh_displays_action'):
            self.refresh_displays_action.setEnabled(False)

    def SetupGameTab(self):
        from PyQt6.QtWidgets import QLineEdit
        from map_preset_loader import list_available_presets, get_map_display_name
        self.layout.addWidget(QLabel("Map Settings:"))
        map_type_layout = QHBoxLayout()
        map_type_layout.addWidget(QLabel("Map Type:"))
        self.map_type_combo = QComboBox()
        # Dynamic map discovery - any map_presets_*.py in directory is available
        self._map_preset_names = []
        self._map_display_names = []
        for preset_name in list_available_presets():
            display_name = get_map_display_name(preset_name) or preset_name.replace("_", " ").title()
            self._map_preset_names.append(preset_name)
            self._map_display_names.append(display_name)
        if not self._map_preset_names:
            self._map_preset_names = ["standard"]
            self._map_display_names = ["Standard"]
        self.map_type_combo.addItems(self._map_display_names)
        self.map_type_combo.setCurrentIndex(0)
        map_type_layout.addWidget(self.map_type_combo)
        self.layout.addLayout(map_type_layout)
        map_size_layout = QHBoxLayout()
        map_size_layout.addWidget(QLabel("Map Width:"))
        self.map_width_spin = QSpinBox()
        self.map_width_spin.setMinimum(10)
        self.map_width_spin.setMaximum(200)
        self.map_width_spin.setValue(100)
        map_size_layout.addWidget(self.map_width_spin)
        map_size_layout.addWidget(QLabel("Map Height:"))
        self.map_height_spin = QSpinBox()
        self.map_height_spin.setMinimum(10)
        self.map_height_spin.setMaximum(200)
        self.map_height_spin.setValue(75)
        map_size_layout.addWidget(self.map_height_spin)
        self.layout.addLayout(map_size_layout)
        self.layout.addWidget(QLabel(""))
        self.layout.addWidget(QLabel("Player Settings:"))
        self.layout.addWidget(QLabel("Number of players:"))
        self.player_count_spin = QSpinBox()
        self.player_count_spin.setMinimum(1)
        self.player_count_spin.setMaximum(8)
        self.player_count_spin.setValue(2)
        self.player_count_spin.valueChanged.connect(self.rebuild_player_name_fields)
        self.layout.addWidget(self.player_count_spin)
        starting_money_layout = QHBoxLayout()
        starting_money_layout.addWidget(QLabel("Starting Money per Player: $"))
        self.starting_money_spin = QSpinBox()
        self.starting_money_spin.setMinimum(1000)
        self.starting_money_spin.setMaximum(1000000)
        self.starting_money_spin.setValue(STARTING_MONEY)
        self.starting_money_spin.setSingleStep(1000)
        starting_money_layout.addWidget(self.starting_money_spin)
        self.layout.addLayout(starting_money_layout)
        replenishment_layout = QHBoxLayout()
        replenishment_layout.addWidget(QLabel("Worker Replenishment Rate:"))
        self.replenishment_rate_spin = QDoubleSpinBox()
        self.replenishment_rate_spin.setMinimum(0.1)
        self.replenishment_rate_spin.setMaximum(5.0)
        self.replenishment_rate_spin.setValue(1.0)
        self.replenishment_rate_spin.setSingleStep(0.1)
        self.replenishment_rate_spin.setDecimals(1)
        replenishment_layout.addWidget(self.replenishment_rate_spin)
        replenishment_layout.addWidget(QLabel("(1.0 = normal, 2.0 = double, 0.5 = half)"))
        self.layout.addLayout(replenishment_layout)
        disaster_frequency_layout = QHBoxLayout()
        disaster_frequency_layout.addWidget(QLabel("Natural Disaster Frequency:"))
        self.disaster_frequency_spin = QDoubleSpinBox()
        self.disaster_frequency_spin.setMinimum(0.1)
        self.disaster_frequency_spin.setMaximum(5.0)
        self.disaster_frequency_spin.setValue(1.0)
        self.disaster_frequency_spin.setSingleStep(0.1)
        self.disaster_frequency_spin.setDecimals(1)
        disaster_frequency_layout.addWidget(self.disaster_frequency_spin)
        disaster_frequency_layout.addWidget(QLabel("(1.0 = normal, 2.0 = twice as often, 0.5 = half as often)"))
        self.layout.addLayout(disaster_frequency_layout)
        marketplace_years_layout = QHBoxLayout()
        marketplace_years_layout.addWidget(QLabel("Marketplace Years Visible:"))
        self.marketplace_years_spin = QSpinBox()
        self.marketplace_years_spin.setMinimum(1)
        self.marketplace_years_spin.setMaximum(200)
        from game_presets import MARKETPLACE_YEARS_VISIBLE
        self.marketplace_years_spin.setValue(MARKETPLACE_YEARS_VISIBLE)
        marketplace_years_layout.addWidget(self.marketplace_years_spin)
        marketplace_years_layout.addWidget(QLabel("(years back to show items in marketplace)"))
        self.layout.addLayout(marketplace_years_layout)
        # Edit mode checkbox
        self.edit_mode_checkbox = QCheckBox("Enable Edit Mode (allows adding/removing population points)")
        self.edit_mode_checkbox.setChecked(False)
        self.layout.addWidget(self.edit_mode_checkbox)
        self.names_container = QVBoxLayout()
        self.name_edits = []
        self.rebuild_player_name_fields(2)
        self.layout.addLayout(self.names_container)
        self.load_game_button = QPushButton("Load Game")
        self.load_game_button.clicked.connect(self.on_load_game_clicked)
        self.layout.addWidget(self.load_game_button)
        self.start_game_button = QPushButton("Start Game")
        self.start_game_button.clicked.connect(self.on_start_game_clicked)
        self.layout.addWidget(self.start_game_button)
    
    def rebuild_player_name_fields(self, count):
        
        from PyQt6.QtWidgets import QLineEdit
        for i in reversed(range(self.names_container.count())):
            w = self.names_container.itemAt(i).widget()
            if w:
                w.setParent(None)
        self.name_edits.clear()
        for i in range(count):
            label = QLabel(f"Player {i+1} name:")
            edit = QLineEdit()
            edit.setPlaceholderText(f"Player{i+1}")
            self.names_container.addWidget(label)
            self.names_container.addWidget(edit)
            self.name_edits.append(edit)
    
    def on_start_game_clicked(self):
        count = self.player_count_spin.value()
        names = []
        for i in range(count):
            if i < len(self.name_edits):
                txt = self.name_edits[i].text().strip()
            else:
                txt = f"Player{i+1}"
            if not txt:
                txt = f"Player{i+1}"
            names.append(txt)
        
        map_width = self.map_width_spin.value()
        map_height = self.map_height_spin.value()
        starting_money = self.starting_money_spin.value()
        worker_replenishment_rate = self.replenishment_rate_spin.value()
        natural_disaster_frequency = self.disaster_frequency_spin.value()
        marketplace_years_visible = self.marketplace_years_spin.value()
        edit_mode_enabled = self.edit_mode_checkbox.isChecked()
        self.start_game_with_players(names, map_width, map_height, starting_money, worker_replenishment_rate, natural_disaster_frequency, marketplace_years_visible, edit_mode_enabled)

    def GetPlayerInfoButton(self):
        self.get_player_info_button = QPushButton("Get Player Info")
        self.get_player_info_button.clicked.connect(self.on_player_info_button_clicked)
        self.info_layout.addWidget(self.get_player_info_button)

    def start_game_with_players(self, player_names: list[str], map_width: int = 100, map_height: int = 100, starting_money: int = 20000, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, marketplace_years_visible: int = None, edit_mode_enabled: bool = False):
        # Map selection from dynamic preset discovery (map type combo)
        combo_index = self.map_type_combo.currentIndex()
        map_preset_name = self._map_preset_names[combo_index] if combo_index < len(self._map_preset_names) else "standard"
        
        self.operations = Operations(
            player_names, map_width, map_height, starting_money,
            worker_replenishment_rate, natural_disaster_frequency,
            map_preset_name=map_preset_name,
            marketplace_years_visible=marketplace_years_visible
        )
        self.edit_mode_enabled = edit_mode_enabled
        self.edit_history = []
        
        if hasattr(self, 'player_tabs_dict'):
            for player_id, player_tabs_dict in self.player_tabs_dict.items():
                edit_mode_section = player_tabs_dict.get('edit_mode_section')
                undo_btn = player_tabs_dict.get('undo_btn')
                if edit_mode_section:
                    edit_mode_section.setVisible(edit_mode_enabled)
                if undo_btn:
                    undo_btn.setEnabled(False)  # Disabled until first edit
        if self.status_bar is None:
            central_layout = self.centralWidget().layout()
            self.status_bar = QWidget()
            self.status_bar_layout = QHBoxLayout()
            self.status_bar.setLayout(self.status_bar_layout)
            self.status_year_label = QLabel("Year: 1830")
            self.status_end_year_btn = QPushButton("End Year")
            self.status_end_year_btn.clicked.connect(self.advance_year_button_clicked)
            self.status_bar_layout.addWidget(self.status_year_label)
            self.status_bar_layout.addStretch()
            for i in range(8):
                money_label = QLabel("")
                self.status_money_labels[i] = money_label
                self.status_bar_layout.addWidget(money_label)
            self.status_bar_layout.addWidget(self.status_end_year_btn)
            self.status_save_game_btn = QPushButton("Save Game")
            self.status_save_game_btn.clicked.connect(self.on_save_game_clicked)
            self.status_bar_layout.addWidget(self.status_save_game_btn)
            self.status_end_game_btn = QPushButton("End Game")
            self.status_end_game_btn.clicked.connect(self.on_end_game_clicked)
            self.status_bar_layout.addWidget(self.status_end_game_btn)
            central_layout.addWidget(self.status_bar)
        # Enable save game and game operations in menu - available at any time during game
        if hasattr(self, 'save_game_action'):
            self.save_game_action.setEnabled(True)
        if hasattr(self, 'advance_year_action'):
            self.advance_year_action.setEnabled(True)
        if hasattr(self, 'refresh_displays_action'):
            self.refresh_displays_action.setEnabled(True)
        
        title = "Start Game"
        players_str = ", ".join([self.operations.get_player_name(player) for player in self.operations.get_players()])
        text = f"The game has started. The players are:\n{players_str}"
        QMessageBox.information(self, title, text)
        try:
            self.tabs.removeTab(0)
        except Exception:
            pass
        players = self.operations.get_players()
        for player in players:
            player_id = self.operations.get_player_id(player)
            player_name = player.get_name()
            player_tabs = self._create_player_tabs(player)
            self.tabs.addTab(player_tabs, player_name)
        
        self._create_rules_tab()
        self._create_vehicles_tab()
        
        self.current_player_index = 0
        if players:
            first_player = players[0]
            first_player_id = self.operations.get_player_id(first_player)
            self.lines_tab = self.player_tabs_dict[first_player_id]['lines_tab']
            self.lines_layout = self.player_tabs_dict[first_player_id]['lines_layout']
            self.rolling_stock_tab = self.player_tabs_dict[first_player_id]['rolling_stock_tab']
            self.rolling_stock_tabs = self.player_tabs_dict[first_player_id]['rolling_stock_tabs']
            self.rolling_stock_trainsets_tab = self.player_tabs_dict[first_player_id]['rolling_stock_trainsets_tab']
            self.rolling_stock_trainsets_layout = self.player_tabs_dict[first_player_id]['rolling_stock_trainsets_layout']
            self.rolling_stock_locomotives_tab = self.player_tabs_dict[first_player_id]['rolling_stock_locomotives_tab']
            self.rolling_stock_locomotives_layout = self.player_tabs_dict[first_player_id]['rolling_stock_locomotives_layout']
            self.rolling_stock_railcars_tab = self.player_tabs_dict[first_player_id]['rolling_stock_railcars_tab']
            self.rolling_stock_railcars_layout = self.player_tabs_dict[first_player_id]['rolling_stock_railcars_layout']
            self.workers_tab = self.player_tabs_dict[first_player_id]['workers_tab']
            self.workers_layout = self.player_tabs_dict[first_player_id]['workers_layout']
            self.law_tab = self.player_tabs_dict[first_player_id]['law_tab']
            self.law_layout = self.player_tabs_dict[first_player_id]['law_layout']
        if players:
            for player in players:
                self.DisplayRollingStockInfo(player)
        self.DisplayMarketplaceInfo()
        self.DisplayMapInfo()
        if players:
            for player in players:
                self.DisplayLinesControls(player)
                self.DisplayWorkersControls(player)
                try:
                    self.DisplayLawInfo(player)
                except Exception as e:
                    player_id = self.operations.get_player_id(player)
                    if player_id in self.player_tabs_dict:
                        self.player_tabs_dict[player_id]['law_layout'].addWidget(QLabel(f"Error loading lawsuit info: {str(e)}"))
        self.refresh_construction_tab()
        self.update_status_bar()

    def _create_player_tabs(self, player):
        player_id = self.operations.get_player_id(player)
        player_name = player.get_name()
        player_tabs = QTabWidget()
        player_tabs_dict = {}
        lines_tab = QWidget()
        lines_layout = QVBoxLayout()
        lines_tab.setLayout(lines_layout)
        player_tabs.addTab(lines_tab, "Lines")
        self.CreateLineButton(lines_layout)
        self.UpgradeLineButton(lines_layout)
        self.ExtendLineButton(lines_layout)
        self.CreateStationButton(lines_layout)
        self.ExtendStationButton(lines_layout)
        self.AssignTrainsetButton(lines_layout)
        self.UnassignTrainsetButton(lines_layout)
        rolling_stock_tab = QWidget()
        rolling_stock_main_layout = QVBoxLayout()
        rolling_stock_tab.setLayout(rolling_stock_main_layout)
        rolling_stock_tabs = QTabWidget()
        rolling_stock_main_layout.addWidget(rolling_stock_tabs)
        rolling_stock_trainsets_tab = QWidget()
        rolling_stock_trainsets_layout = QVBoxLayout()
        rolling_stock_trainsets_tab.setLayout(rolling_stock_trainsets_layout)
        rolling_stock_tabs.addTab(rolling_stock_trainsets_tab, "Trainsets")
        rolling_stock_locomotives_tab = QWidget()
        rolling_stock_locomotives_layout = QVBoxLayout()
        rolling_stock_locomotives_tab.setLayout(rolling_stock_locomotives_layout)
        rolling_stock_tabs.addTab(rolling_stock_locomotives_tab, "Locomotives")
        rolling_stock_railcars_tab = QWidget()
        rolling_stock_railcars_layout = QVBoxLayout()
        rolling_stock_railcars_tab.setLayout(rolling_stock_railcars_layout)
        rolling_stock_tabs.addTab(rolling_stock_railcars_tab, "Railcars")
        player_tabs.addTab(rolling_stock_tab, "Rolling Stock")
        self.CreateTrainsetButton(rolling_stock_trainsets_layout)
        self.AssembleTrainsetButton(rolling_stock_trainsets_layout)
        self.AddRailcarButton(rolling_stock_trainsets_layout)
        self.SellTrainsetButton(rolling_stock_trainsets_layout)
        self.SellToHistoricalSocietyButton(rolling_stock_trainsets_layout)
        self.DisassembleTrainsetButton(rolling_stock_trainsets_layout)
        workers_tab = QWidget()
        workers_layout = QVBoxLayout()
        workers_tab.setLayout(workers_layout)
        player_tabs.addTab(workers_tab, "Workers")
        self.add_year_display_to_tab(workers_layout, include_end_year_button=False)
        law_tab = QWidget()
        law_layout = QVBoxLayout()
        law_tab.setLayout(law_layout)
        player_tabs.addTab(law_tab, "Law")
        self.add_year_display_to_tab(law_layout, include_end_year_button=False)
        marketplace_tab = QWidget()
        marketplace_main_layout = QVBoxLayout()
        marketplace_tab.setLayout(marketplace_main_layout)
        view_marketplace_button = QPushButton("Refresh Marketplace")
        view_marketplace_button.clicked.connect(self.view_marketplace_button_clicked)
        marketplace_main_layout.addWidget(view_marketplace_button)
        marketplace_tabs = QTabWidget()
        marketplace_main_layout.addWidget(marketplace_tabs)
        marketplace_lines_tab = QWidget()
        marketplace_lines_scroll = QScrollArea()
        marketplace_lines_scroll.setWidgetResizable(True)
        marketplace_lines_scroll.setMaximumHeight(400)
        marketplace_lines_widget = QWidget()
        marketplace_lines_layout = QVBoxLayout()
        marketplace_lines_widget.setLayout(marketplace_lines_layout)
        marketplace_lines_scroll.setWidget(marketplace_lines_widget)
        lines_tab_layout = QVBoxLayout()
        lines_tab_layout.addWidget(marketplace_lines_scroll)
        marketplace_lines_tab.setLayout(lines_tab_layout)
        marketplace_tabs.addTab(marketplace_lines_tab, "Lines")
        
        marketplace_trainsets_tab = QWidget()
        marketplace_trainsets_scroll = QScrollArea()
        marketplace_trainsets_scroll.setWidgetResizable(True)
        marketplace_trainsets_scroll.setMaximumHeight(400)
        marketplace_trainsets_widget = QWidget()
        marketplace_trainsets_layout = QVBoxLayout()
        marketplace_trainsets_widget.setLayout(marketplace_trainsets_layout)
        marketplace_trainsets_scroll.setWidget(marketplace_trainsets_widget)
        trainsets_tab_layout = QVBoxLayout()
        trainsets_tab_layout.addWidget(marketplace_trainsets_scroll)
        marketplace_trainsets_tab.setLayout(trainsets_tab_layout)
        marketplace_tabs.addTab(marketplace_trainsets_tab, "Trainsets")
        
        marketplace_locomotives_tab = QWidget()
        marketplace_locomotives_scroll = QScrollArea()
        marketplace_locomotives_scroll.setWidgetResizable(True)
        marketplace_locomotives_scroll.setMaximumHeight(400)
        marketplace_locomotives_widget = QWidget()
        marketplace_locomotives_layout = QVBoxLayout()
        marketplace_locomotives_widget.setLayout(marketplace_locomotives_layout)
        marketplace_locomotives_scroll.setWidget(marketplace_locomotives_widget)
        locomotives_tab_layout = QVBoxLayout()
        locomotives_tab_layout.addWidget(marketplace_locomotives_scroll)
        marketplace_locomotives_tab.setLayout(locomotives_tab_layout)
        marketplace_tabs.addTab(marketplace_locomotives_tab, "Locomotives")
        
        marketplace_ferries_tab = QWidget()
        marketplace_ferries_scroll = QScrollArea()
        marketplace_ferries_scroll.setWidgetResizable(True)
        marketplace_ferries_scroll.setMaximumHeight(400)
        marketplace_ferries_widget = QWidget()
        marketplace_ferries_layout = QVBoxLayout()
        marketplace_ferries_widget.setLayout(marketplace_ferries_layout)
        marketplace_ferries_scroll.setWidget(marketplace_ferries_widget)
        ferries_tab_layout = QVBoxLayout()
        ferries_tab_layout.addWidget(marketplace_ferries_scroll)
        marketplace_ferries_tab.setLayout(ferries_tab_layout)
        marketplace_tabs.addTab(marketplace_ferries_tab, "Ferries")
        
        marketplace_horses_trolleys_tab = QWidget()
        marketplace_horses_trolleys_scroll = QScrollArea()
        marketplace_horses_trolleys_scroll.setWidgetResizable(True)
        marketplace_horses_trolleys_scroll.setMaximumHeight(400)
        marketplace_horses_trolleys_widget = QWidget()
        marketplace_horses_trolleys_layout = QVBoxLayout()
        marketplace_horses_trolleys_widget.setLayout(marketplace_horses_trolleys_layout)
        marketplace_horses_trolleys_scroll.setWidget(marketplace_horses_trolleys_widget)
        horses_trolleys_tab_layout = QVBoxLayout()
        horses_trolleys_tab_layout.addWidget(marketplace_horses_trolleys_scroll)
        marketplace_horses_trolleys_tab.setLayout(horses_trolleys_tab_layout)
        marketplace_tabs.addTab(marketplace_horses_trolleys_tab, "Horses & Trolleys")
        
        marketplace_railcars_tab = QWidget()
        marketplace_railcars_scroll = QScrollArea()
        marketplace_railcars_scroll.setWidgetResizable(True)
        marketplace_railcars_scroll.setMaximumHeight(400)
        marketplace_railcars_widget = QWidget()
        marketplace_railcars_layout = QVBoxLayout()
        marketplace_railcars_widget.setLayout(marketplace_railcars_layout)
        marketplace_railcars_scroll.setWidget(marketplace_railcars_widget)
        railcars_tab_layout = QVBoxLayout()
        railcars_tab_layout.addWidget(marketplace_railcars_scroll)
        marketplace_railcars_tab.setLayout(railcars_tab_layout)
        marketplace_tabs.addTab(marketplace_railcars_tab, "Railcars")
        marketplace_resources_tab = QWidget()
        marketplace_resources_layout = QVBoxLayout()
        marketplace_resources_tab.setLayout(marketplace_resources_layout)
        marketplace_tabs.addTab(marketplace_resources_tab, "Resources")
        marketplace_workers_tab = QWidget()
        marketplace_workers_layout = QVBoxLayout()
        marketplace_workers_tab.setLayout(marketplace_workers_layout)
        marketplace_tabs.addTab(marketplace_workers_tab, "Workers")
        player_tabs.addTab(marketplace_tab, "Marketplace")
        map_tab = QWidget()
        # Create main horizontal layout: sidebar on left, map on right
        map_main_layout = QHBoxLayout()
        map_tab.setLayout(map_main_layout)
        
        # Create sidebar for map controls
        map_sidebar = QWidget()
        map_sidebar.setFixedWidth(250)  # Fixed width sidebar
        map_sidebar_layout = QVBoxLayout()
        map_sidebar.setLayout(map_sidebar_layout)
        map_sidebar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)  # Ensure clickable
        
        # Map info section
        map_info_label = QLabel("Map Info")
        map_info_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        map_sidebar_layout.addWidget(map_info_label)
        map_info_text = QLabel("")
        map_info_text.setWordWrap(True)
        map_sidebar_layout.addWidget(map_info_text)
        
        map_sidebar_layout.addWidget(QLabel(""))  # Spacer
        
        # Line creation section
        line_creation_label = QLabel("Create Line")
        line_creation_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        map_sidebar_layout.addWidget(line_creation_label)
        
        create_line_btn = QPushButton("Start New Line")
        map_sidebar_layout.addWidget(create_line_btn)
        
        line_name_input = QLineEdit()
        line_name_input.setPlaceholderText("Line name...")
        map_sidebar_layout.addWidget(line_name_input)
        
        line_points_label = QLabel("Points: 0")
        map_sidebar_layout.addWidget(line_points_label)
        
        finish_line_btn = QPushButton("Finish Line")
        finish_line_btn.setEnabled(False)
        map_sidebar_layout.addWidget(finish_line_btn)
        
        cancel_line_btn = QPushButton("Cancel")
        cancel_line_btn.setEnabled(False)
        map_sidebar_layout.addWidget(cancel_line_btn)
        
        extend_line_btn = QPushButton("Extend Line")
        map_sidebar_layout.addWidget(extend_line_btn)
        
        extend_line_combo = QComboBox()
        extend_line_combo.addItem("Select line...")
        map_sidebar_layout.addWidget(extend_line_combo)
        
        extend_line_points_label = QLabel("Points: 0")
        map_sidebar_layout.addWidget(extend_line_points_label)
        
        finish_extend_btn = QPushButton("Finish Extend")
        finish_extend_btn.setEnabled(False)
        map_sidebar_layout.addWidget(finish_extend_btn)
        
        cancel_extend_btn = QPushButton("Cancel Extend")
        cancel_extend_btn.setEnabled(False)
        map_sidebar_layout.addWidget(cancel_extend_btn)
        
        map_sidebar_layout.addWidget(QLabel(""))  # Spacer
        
        # Station widgets kept hidden - used when "Add Station" on map is clicked (with confirm dialog)
        create_station_btn = None
        station_name_input = QLineEdit()
        station_name_input.setPlaceholderText("Station name...")
        station_line_combo = QComboBox()
        station_line_combo.addItem("Select line...")
        _hidden_station_widget = QWidget()
        _hidden_station_layout = QVBoxLayout()
        _hidden_station_widget.setLayout(_hidden_station_layout)
        _hidden_station_layout.addWidget(station_name_input)
        _hidden_station_layout.addWidget(station_line_combo)
        _hidden_station_widget.setVisible(False)
        map_sidebar_layout.addWidget(_hidden_station_widget)
        
        # Edit mode section (only shown when edit mode is enabled)
        edit_mode_section = QWidget()
        edit_mode_section_layout = QVBoxLayout()
        edit_mode_section.setLayout(edit_mode_section_layout)
        edit_mode_section.setVisible(False)  # Hidden by default
        map_sidebar_layout.addWidget(edit_mode_section)
        
        edit_mode_label = QLabel("Edit Mode")
        edit_mode_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        edit_mode_section_layout.addWidget(edit_mode_label)
        
        undo_btn = QPushButton("Undo Last Edit")
        undo_btn.setEnabled(False)
        undo_btn.clicked.connect(self._undo_last_edit)
        edit_mode_section_layout.addWidget(undo_btn)
        
        map_sidebar_layout.addStretch()  # Push everything to top
        
        # Add sidebar to main layout (fixed width, no stretch)
        print(f"[DEBUG]   - Adding sidebar to map_main_layout")
        map_main_layout.addWidget(map_sidebar, 0)  # Stretch factor 0 - fixed width
        print(f"[DEBUG]   - Sidebar added, map_main_layout count: {map_main_layout.count()}")
        
        # Create map layout for the actual map display
        map_layout = QVBoxLayout()
        print(f"[DEBUG]   - map_layout created: {map_layout}")
        map_main_layout.addLayout(map_layout, 1)  # Stretch factor 1 for map - takes remaining space
        print(f"[DEBUG]   - map_layout added to map_main_layout, count: {map_main_layout.count()}")
        
        print(f"[DEBUG]   - Adding map_tab to player_tabs")
        map_tab_index = player_tabs.addTab(map_tab, "Map")
        print(f"[DEBUG]   - map_tab added, player_tabs tab count: {player_tabs.count()}")
        # Don't add year display to map tab - it covers the sidebar buttons
        
        # Add tab change handler to create QWebEngineView when map tab becomes visible
        def on_player_tab_changed(index):
            """Handle tab changes - create QWebEngineView when map tab becomes visible"""
            try:
                if index == map_tab_index:
                    # Map tab is now visible - ensure QWebEngineView is created
                    if f'map_web_view_{player_id}' not in player_tabs_dict or player_tabs_dict.get(f'map_web_view_{player_id}') is None:
                        # Trigger map display to create QWebEngineView
                        # Use QTimer to defer creation until after tab switch completes
                        from PyQt6.QtCore import QTimer
                        def create_webview_delayed():
                            try:
                                self.DisplayMapInfo()
                            except Exception as e:
                                print(f"    - Error creating webview on tab change: {e}")
                        QTimer.singleShot(100, create_webview_delayed)  # 100ms delay
            except Exception as e:
                print(f"    - Error in tab change handler: {e}")
        
        player_tabs.currentChanged.connect(on_player_tab_changed)
        
        print(f"[DEBUG]   - Storing references in player_tabs_dict")
        player_tabs_dict['map_sidebar'] = map_sidebar
        player_tabs_dict['map_info_text'] = map_info_text
        player_tabs_dict['create_line_btn'] = create_line_btn
        player_tabs_dict['line_name_input'] = line_name_input
        player_tabs_dict['line_points_label'] = line_points_label
        player_tabs_dict['map_main_layout'] = map_main_layout
        player_tabs_dict['map_tab'] = map_tab
        player_tabs_dict['map_layout'] = map_layout
        print(f"[DEBUG]   - References stored. player_tabs_dict keys: {list(player_tabs_dict.keys())}")
        print(f"[DEBUG]   - map_layout stored: {player_tabs_dict.get('map_layout')}")
        print(f"[DEBUG]   - map_tab stored: {player_tabs_dict.get('map_tab')}")
        print(f"[DEBUG] Map tab creation complete for player {player_id}\n")
        player_tabs_dict['finish_line_btn'] = finish_line_btn
        player_tabs_dict['cancel_line_btn'] = cancel_line_btn
        player_tabs_dict['extend_line_btn'] = extend_line_btn
        player_tabs_dict['extend_line_combo'] = extend_line_combo
        player_tabs_dict['extend_line_points_label'] = extend_line_points_label
        player_tabs_dict['finish_extend_btn'] = finish_extend_btn
        player_tabs_dict['cancel_extend_btn'] = cancel_extend_btn
        player_tabs_dict['create_station_btn'] = create_station_btn
        player_tabs_dict['station_name_input'] = station_name_input
        player_tabs_dict['station_line_combo'] = station_line_combo
        
        self._connect_map_sidebar_buttons(player_id, player_tabs_dict)
        construction_tab = QWidget()
        construction_layout = QVBoxLayout()
        construction_tab.setLayout(construction_layout)
        construction_subtabs = QTabWidget()
        construction_layout.addWidget(construction_subtabs)
        in_progress_tab = QWidget()
        in_progress_scroll = QScrollArea()
        in_progress_scroll.setWidgetResizable(True)
        in_progress_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        in_progress_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        in_progress_widget = QWidget()
        in_progress_layout = QVBoxLayout()
        in_progress_widget.setLayout(in_progress_layout)
        in_progress_scroll.setWidget(in_progress_widget)
        construction_subtabs.addTab(in_progress_scroll, "In-Progress Jobs")
        complete_jobs_tab = QWidget()
        complete_jobs_scroll = QScrollArea()
        complete_jobs_scroll.setWidgetResizable(True)
        complete_jobs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        complete_jobs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        complete_jobs_widget = QWidget()
        complete_jobs_layout = QVBoxLayout()
        complete_jobs_widget.setLayout(complete_jobs_layout)
        complete_jobs_scroll.setWidget(complete_jobs_widget)
        construction_subtabs.addTab(complete_jobs_scroll, "Complete Jobs")
        player_tabs.addTab(construction_tab, "Construction")
        # Refresh construction tab when it's opened/switched
        def on_construction_tab_changed(index):
            if player_tabs.currentWidget() == construction_tab:
                self.refresh_construction_tab()
        player_tabs.currentChanged.connect(on_construction_tab_changed)
        # Also refresh when construction subtabs are switched
        def on_construction_subtab_changed(index):
            self.refresh_construction_tab()
        construction_subtabs.currentChanged.connect(on_construction_subtab_changed)
        player_tabs_dict['lines_tab'] = lines_tab
        player_tabs_dict['lines_layout'] = lines_layout
        player_tabs_dict['rolling_stock_tab'] = rolling_stock_tab
        player_tabs_dict['rolling_stock_tabs'] = rolling_stock_tabs
        player_tabs_dict['rolling_stock_trainsets_tab'] = rolling_stock_trainsets_tab
        player_tabs_dict['rolling_stock_trainsets_layout'] = rolling_stock_trainsets_layout
        player_tabs_dict['rolling_stock_locomotives_tab'] = rolling_stock_locomotives_tab
        player_tabs_dict['rolling_stock_locomotives_layout'] = rolling_stock_locomotives_layout
        player_tabs_dict['rolling_stock_railcars_tab'] = rolling_stock_railcars_tab
        player_tabs_dict['rolling_stock_railcars_layout'] = rolling_stock_railcars_layout
        player_tabs_dict['workers_tab'] = workers_tab
        player_tabs_dict['workers_layout'] = workers_layout
        player_tabs_dict['law_tab'] = law_tab
        player_tabs_dict['law_layout'] = law_layout
        player_tabs_dict['marketplace_tab'] = marketplace_tab
        player_tabs_dict['marketplace_main_layout'] = marketplace_main_layout
        player_tabs_dict['marketplace_tabs'] = marketplace_tabs
        player_tabs_dict['marketplace_lines_tab'] = marketplace_lines_tab
        player_tabs_dict['marketplace_lines_layout'] = marketplace_lines_layout
        player_tabs_dict['marketplace_trainsets_tab'] = marketplace_trainsets_tab
        player_tabs_dict['marketplace_trainsets_layout'] = marketplace_trainsets_layout
        player_tabs_dict['marketplace_locomotives_tab'] = marketplace_locomotives_tab
        player_tabs_dict['marketplace_locomotives_layout'] = marketplace_locomotives_layout
        player_tabs_dict['marketplace_ferries_tab'] = marketplace_ferries_tab
        player_tabs_dict['marketplace_ferries_layout'] = marketplace_ferries_layout
        player_tabs_dict['marketplace_horses_trolleys_tab'] = marketplace_horses_trolleys_tab
        player_tabs_dict['marketplace_horses_trolleys_layout'] = marketplace_horses_trolleys_layout
        player_tabs_dict['marketplace_railcars_tab'] = marketplace_railcars_tab
        player_tabs_dict['marketplace_railcars_layout'] = marketplace_railcars_layout
        player_tabs_dict['marketplace_resources_tab'] = marketplace_resources_tab
        player_tabs_dict['marketplace_resources_layout'] = marketplace_resources_layout
        player_tabs_dict['marketplace_workers_tab'] = marketplace_workers_tab
        player_tabs_dict['marketplace_workers_layout'] = marketplace_workers_layout
        player_tabs_dict['map_tab'] = map_tab
        player_tabs_dict['map_layout'] = map_layout
        player_tabs_dict['construction_tab'] = construction_tab
        player_tabs_dict['construction_layout'] = construction_layout
        player_tabs_dict['construction_subtabs'] = construction_subtabs
        player_tabs_dict['in_progress_layout'] = in_progress_layout
        player_tabs_dict['complete_jobs_layout'] = complete_jobs_layout
        self.player_tabs_dict[player_id] = player_tabs_dict
        return player_tabs

    def _create_rules_tab(self):
        """Create a Rules tab that displays a README file with game rules"""
        rules_tab = QWidget()
        rules_layout = QVBoxLayout()
        rules_tab.setLayout(rules_layout)
        
        rules_browser = QTextBrowser()
        rules_browser.setReadOnly(True)
        
        script_dir = Path(__file__).parent.absolute()
        
        rules_file = None
        for filename in ['RULES.md', 'GAME_RULES.md', 'README.md']:
            file_path = script_dir / filename
            if file_path.exists():
                rules_file = file_path
                break
        
        if rules_file:
            try:
                with open(rules_file, 'r', encoding='utf-8') as f:
                    rules_content = f.read()
                rules_browser.setPlainText(rules_content)
            except Exception as e:
                rules_browser.setPlainText(f"Error loading rules file: {str(e)}\n\nPlease create a RULES.md or GAME_RULES.md file in the game directory.")
        else:
            rules_browser.setPlainText(
                "# Game Rules\n\n"
                "Please create a RULES.md or GAME_RULES.md file in the game directory to display game rules here.\n\n"
                "The file should contain markdown-formatted text explaining the rules of the Transit Game."
            )
        
        rules_layout.addWidget(rules_browser)
        self.tabs.addTab(rules_tab, "Rules")
    
    def _create_vehicles_tab(self):
        """Create a Vehicles tab that displays a README file with vehicle details"""
        vehicles_tab = QWidget()
        vehicles_layout = QVBoxLayout()
        vehicles_tab.setLayout(vehicles_layout)
        
        vehicles_browser = QTextBrowser()
        vehicles_browser.setReadOnly(True)
        
        script_dir = Path(__file__).parent.absolute()
        
        vehicles_file = None
        for filename in ['VEHICLES.md', 'VEHICLE_DETAILS.md', 'VEHICLES_README.md']:
            file_path = script_dir / filename
            if file_path.exists():
                vehicles_file = file_path
                break
        
        if vehicles_file:
            try:
                with open(vehicles_file, 'r', encoding='utf-8') as f:
                    vehicles_content = f.read()
                vehicles_browser.setPlainText(vehicles_content)
            except Exception as e:
                vehicles_browser.setPlainText(f"Error loading vehicles file: {str(e)}\n\nPlease create a VEHICLES.md or VEHICLE_DETAILS.md file in the game directory.")
        else:
            vehicles_browser.setPlainText(
                "# Vehicle Details\n\n"
                "Please create a VEHICLES.md or VEHICLE_DETAILS.md file in the game directory to display vehicle details here.\n\n"
                "The file should contain markdown-formatted text explaining all available vehicles, their specifications, and capabilities."
            )
        
        vehicles_layout.addWidget(vehicles_browser)
        self.tabs.addTab(vehicles_tab, "Vehicles")

    def on_player_info_button_clicked(self):
        if self.operations is None:
            QMessageBox.information(self, "Player Info", "Game has not started yet.")
            return
        players = self.operations.get_active_players()
        if not players:
            QMessageBox.information(self, "Player Info", "No active players remaining.")
            return
        out = []
        for p in players:
            header = f"{self.operations.get_player_name(p)}: Money: ${self.operations.get_player_money(p)}"
            out.append(header)
            current_year = self.operations.get_year()
            line_names = self.operations.get_player_line_names(p)
            if line_names:
                out.append("  Lines:")
                for lname in line_names:
                    line = self.operations.get_map_lines().get(lname)
                    if line:
                        length = len(self.operations.get_line_route(line))
                        profit = self.operations.calc_line_profit(line, current_year)
                        mait = self.operations.calc_line_maintenance(line)
                        out.append(f"    - {lname}: points={length}, profit=${int(profit)}, maintenance=${int(mait)}")
                    else:
                        out.append(f"    - {lname}")
            else:
                out.append("  Lines: none")
            trainsets = self.operations.get_player_trainsets(p)
            if trainsets:
                out.append("  Trainsets:")
                for i, ts in enumerate(trainsets):
                    try:
                        if ts is None:
                            out.append(f"    - Trainset {i}: [None]")
                            continue
                        loco_name = self.operations.get_trainset_locomotive_name(ts)
                        cars = len(self.operations.get_trainset_cars(ts))
                        cap = 0
                        if hasattr(ts, 'get_capacity'):
                            try:
                                cap = self.operations.get_trainset_capacity(ts)
                            except Exception:
                                pass
                        mait = 0
                        try:
                            mait = self.operations.get_trainset_maintenance_costs(ts)
                        except Exception:
                            pass
                        assign = None
                        if hasattr(ts, 'get_line_assignment'):
                            try:
                                assign = self.operations.get_trainset_line_assignment(ts)
                            except Exception:
                                pass
                        out.append(f"    - Trainset {i}: loco={loco_name}, cars={cars}, cap={cap}, maintenance=${mait}, assigned_to={assign}")
                    except Exception as e:
                        import traceback
                        error_msg = f"Error: {str(e)}"
                        out.append(f"    - Trainset {i}: {error_msg}")
            else:
                out.append("  Trainsets: none")
            try:
                finances = self.operations.get_player_financial_summary(p, current_year)
                out.append("  Financial Summary:")
                out.append(f"    - Profit (lines): ${int(finances['profit'])}")
                out.append(f"    - Trainset maintenance: ${int(finances['trainset_maintenance'])}")
                out.append(f"    - Resource charges: ${int(finances['resource_charge'])}")
                out.append(f"    - Line maintenance: ${int(finances['line_maintenance'])}")
                out.append(f"    - Taxes: poll=${finances['poll_tax']}, property=${finances['property_tax']}")
                if finances['worker_costs'] > 0:
                    out.append(f"    - Worker costs: ${int(finances['worker_costs'])}")
                out.append(f"    - Net this year: ${int(finances['net'])}")
            except Exception as e:
                out.append(f"  Financial Summary: Error calculating finances: {str(e)}")

        QMessageBox.information(self, "Player Info", "\n".join(out))

    def on_player_changed(self):
        
        selected_name = self.player_combo.currentText()
        for idx, p in enumerate(self.operations.get_players()):
            if self.operations.get_player_name(p) == selected_name and not self.operations.is_player_eliminated(p):
                self.current_player_index = idx
                self.operations.set_current_player_index(idx)
                break
    
    def check_and_eliminate_player(self, player):
        was_eliminated = self.operations.check_and_eliminate_player(player)
            
        if was_eliminated:
            player_name = self.operations.get_player_name(player)
            loss_message = (
                f"Well, well. {player_name} has gone broke and is out of the game. "
                "Did you think running a transit empire was going to be easy? Construction costs, maintenance, "
                "workers, lawsuits, strikes, and the occasional disaster—and you couldn't even keep the lights on. "
                "Maybe stick to Monopoly next time.\n\n"
                "You lost because you spent more than you made. Too much building, too little revenue, or one too many "
                "lawsuits and strikes. The game doesn't care how close you were—zero money means you're done. "
                "No bailout, no second chances. Consider it a lesson in fiscal responsibility.\n\n"
                "All of this player's lines and trainsets have been removed. Try not to blow it again."
            )
            QMessageBox.warning(self, "Player Eliminated", loss_message)
            
            self.update_player_combo()
            
            active_players = self.operations.get_active_players()
            if not active_players:
                game_over_message = (
                    "Everybody's broke. All players have been eliminated—congratulations on the group failure. "
                    "You had one job: run a transit system without going bankrupt. Construction, maintenance, "
                    "workers, lawsuits, strikes… and not a single one of you could make it work. Impressive.\n\n"
                    "You all ran out of money because none of you managed to bring in more than you spent. "
                    "No bailouts here; the game doesn't reward mediocrity. Consider it a collective lesson in "
                    "how not to run a railroad.\n\n"
                    "No one wins. The board is reset. Maybe next time try not to burn through the cash so fast."
                )
                QMessageBox.information(self, "Game Over", game_over_message)
            else:
                
                current_player = self.operations.get_current_player(self.current_player_index)
                if current_player is None or current_player == player:
                    
                    for idx, p in enumerate(self.operations.get_players()):
                        if p == active_players[0]:
                            self.current_player_index = idx
                            break
                    self.update_player_combo()
    
    def update_player_combo(self):
        if not hasattr(self, 'player_combo'):
            return
        current_text = self.player_combo.currentText() if self.player_combo.count() > 0 else None
        self.player_combo.clear()
        active_players = self.operations.get_active_players()
        player_names_list = [self.operations.get_player_name(p) for p in active_players]
        self.player_combo.addItems(player_names_list)
        if current_text and current_text in player_names_list:
            self.player_combo.setCurrentText(current_text)
        elif player_names_list:
            self.player_combo.setCurrentIndex(0)
        if self.player_combo.count() > 0:
            selected_name = self.player_combo.currentText()
            for idx, p in enumerate(self.operations.get_players()):
                if self.operations.get_player_name(p) == selected_name and not self.operations.is_player_eliminated(p):
                    self.current_player_index = idx
                    self.operations.set_current_player_index(idx)
                    break
            if active_players:
                for idx, p in enumerate(self.operations.get_players()):
                    if p == active_players[0]:
                        self.current_player_index = idx
                        break

    def CreateLineButton(self, layout=None):
        self.create_line_button = QPushButton("Create Line")
        self.create_line_button.clicked.connect(self.on_create_line_clicked)
        if layout is not None:
            layout.addWidget(self.create_line_button)
        elif hasattr(self, 'lines_layout') and self.lines_layout:
            self.lines_layout.addWidget(self.create_line_button)
        else:
            if hasattr(self, 'info_layout') and self.info_layout:
                self.info_layout.addWidget(self.create_line_button)

    def CreateStationButton(self, layout=None):
        self.create_station_button = QPushButton("Create Station")
        self.create_station_button.clicked.connect(self.on_create_station_clicked)
        if layout is not None:
            layout.addWidget(self.create_station_button)
        elif hasattr(self, 'lines_layout') and self.lines_layout is not None:
            self.lines_layout.addWidget(self.create_station_button)

    def on_create_station_clicked(self):
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        station_info = self.operations.get_station_creation_info(current_player)
        if not station_info['has_lines']:
            QMessageBox.information(self, "Create Station", "You have no lines. Create a line first.")
            return
        
        name_input, ok = QInputDialog.getText(self, "Station Name", "Enter station name:")
        if not ok or not name_input:
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Create Station")
        dlg.setGeometry(200, 200, 600, 500)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select line to add station to:"))
        line_combo = QComboBox()
        line_combo.addItems(station_info['line_names'])
        layout.addWidget(line_combo)
        
        point_combo = QComboBox()
        point_combo_label = QLabel("Select point on line:")
        layout.addWidget(point_combo_label)
        layout.addWidget(point_combo)
        
        lines_group = QGroupBox("Select lines to include (if point is on multiple lines):")
        lines_layout = QVBoxLayout()
        lines_group.setLayout(lines_layout)
        line_checkboxes = {}
        lines_group_added = False
        
        def update_points():
            selected_line_name = line_combo.currentText()
            available_points = self.operations.get_station_creation_available_points(selected_line_name)
            point_combo.clear()
            for i, (x, y) in enumerate(available_points):
                point_combo.addItem(f"Point {i+1}: ({x}, {y})", (x, y))
            if point_combo.count() == 0:
                point_combo.addItem("No available points (all have stations)", None)
        
        def update_line_selection():
            nonlocal lines_group_added
            for checkbox in list(line_checkboxes.values()):
                checkbox.setParent(None)
            line_checkboxes.clear()
            
            if lines_group_added:
                layout.removeWidget(lines_group)
                lines_group_added = False
            
            if point_combo.currentData() is None:
                return
            
            x, y = point_combo.currentData()
            intersecting_lines = self.operations.get_lines_through_point(x, y, current_player)
            selected_line_name = line_combo.currentText()
            intersecting_lines = [ln for ln in intersecting_lines if ln != selected_line_name]
            
            if intersecting_lines:
                for line_name in intersecting_lines:
                    checkbox = QCheckBox(line_name)
                    checkbox.setChecked(False)
                    line_checkboxes[line_name] = checkbox
                    lines_layout.addWidget(checkbox)
                    checkbox.stateChanged.connect(update_cost_display)
                layout.addWidget(lines_group)
                lines_group_added = True
        
        def update_cost_display():
            selected_count = sum(1 for cb in line_checkboxes.values() if cb.isChecked())
            total_cost = self.operations.calculate_station_cost(1 + selected_count)
            cost_label.setText(f"Total cost: ${total_cost} (Base: $500 + $250 per additional line)")
        
        line_combo.currentIndexChanged.connect(update_points)
        point_combo.currentIndexChanged.connect(update_line_selection)
        point_combo.currentIndexChanged.connect(update_cost_display)
        
        cost_label = QLabel("Total cost: $500")
        layout.addWidget(cost_label)
        
        try:
            update_points()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error initializing station dialog: {str(e)}")
            dlg.close()
            return
        
        btn_layout = QHBoxLayout()
        create_btn = QPushButton("Create")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_create():
            selected_line_name = line_combo.currentText()
                
            x, y = point_combo.currentData()
            selected_additional_lines = [line_name for line_name, cb in line_checkboxes.items() if cb.isChecked()]
            all_selected_lines = [selected_line_name] + selected_additional_lines
            
            try:
                result = self.operations.create_station_complete(name_input, x, y, current_player, all_selected_lines)
                self.check_and_eliminate_player(current_player)
                lines_str = ", ".join(result['line_names'])
                QMessageBox.information(dlg, "Station Construction Started", 
                    f"Station '{name_input}' construction started at {result['coordinates']} serving lines: {lines_str}.\n"
                    f"Build time: {result['build_time']} turn(s)\n"
                    f"Cost: ${result['cost']}\nRemaining money: ${result['remaining_money']}")
                dlg.close()
                
                self.refresh_construction_tab()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        
        create_btn.clicked.connect(do_create)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def UpgradeLineButton(self, layout=None):
        self.upgrade_line_button = QPushButton("Upgrade Line")
        self.upgrade_line_button.clicked.connect(self.on_upgrade_line_clicked)
        if layout is not None:
            layout.addWidget(self.upgrade_line_button)
        elif hasattr(self, 'lines_layout') and self.lines_layout:
            self.lines_layout.addWidget(self.upgrade_line_button)
        else:
            if hasattr(self, 'info_layout') and self.info_layout:
                self.info_layout.addWidget(self.upgrade_line_button)
    
    def ExtendLineButton(self, layout=None):
        self.extend_line_button = QPushButton("Extend Line")
        self.extend_line_button.clicked.connect(self.on_extend_line_clicked)
        if layout is not None:
            layout.addWidget(self.extend_line_button)
        elif hasattr(self, 'lines_layout') and self.lines_layout:
            self.lines_layout.addWidget(self.extend_line_button)
        else:
            if hasattr(self, 'info_layout') and self.info_layout:
                self.info_layout.addWidget(self.extend_line_button)
    
    def ExtendStationButton(self, layout=None):
        self.extend_station_button = QPushButton("Extend Station")
        self.extend_station_button.clicked.connect(self.on_extend_station_clicked)
        if layout is not None:
            layout.addWidget(self.extend_station_button)
        elif hasattr(self, 'lines_layout') and self.lines_layout is not None:
            self.lines_layout.addWidget(self.extend_station_button)
    
    def on_extend_station_clicked(self):
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        line_names = self.operations.get_player_line_names(current_player)
        if not line_names:
            QMessageBox.information(self, "Extend Station", "You have no lines to extend stations on.")
            return
        
        all_stations = []
        all_lines = self.operations.get_map_lines()
        for line_name in line_names:
            line = all_lines.get(line_name)
            if line:
                stations = self.operations.get_line_stations(line)
                for station in stations:
                    if station not in all_stations:
                        all_stations.append((station, line_name, line))
        
        if not all_stations:
            QMessageBox.information(self, "Extend Station", "You have no stations to extend.")
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Extend Station")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select Station to Extend:"))
        station_combo = QComboBox()
        for station, line_name, line in all_stations:
            station_combo.addItem(f"{station.name} at ({station.get_x()}, {station.get_y()}) on {line_name}", (station, line_name, line))
        layout.addWidget(station_combo)
        
        layout.addWidget(QLabel("Select Additional Line to Add:"))
        line_combo = QComboBox()
        for line_name in line_names:
            line_combo.addItem(line_name, line_name)
        layout.addWidget(line_combo)
        
        btn_layout = QHBoxLayout()
        extend_btn = QPushButton("Extend")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(extend_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)
        
        def do_extend():
            selected_data = station_combo.currentData()
            if not selected_data:
                return
            station, orig_line_name, orig_line = selected_data
            new_line_name = line_combo.currentData()
            
            if new_line_name in station.get_line_names():
                QMessageBox.warning(dlg, "Error", f"Station '{station.name}' already serves line '{new_line_name}'.")
                return
            
            try:
                self.operations.modify_station_lines(station, line_names_to_add=[new_line_name])
                new_line = all_lines.get(new_line_name)
                if new_line and station not in new_line.get_stations():
                    new_line.add_station(station)
                QMessageBox.information(dlg, "Station Extended", f"Station '{station.name}' now serves line '{new_line_name}'.")
                dlg.close()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        
        extend_btn.clicked.connect(do_extend)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_upgrade_line_clicked(self):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        line_names = self.operations.get_player_line_names(current_player)
        if not line_names:
            QMessageBox.information(self, "Upgrade Line", "You have no lines to upgrade.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Upgrade Line")
        dlg.setGeometry(200, 200, 400, 200)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Select Line to Upgrade:"))
        line_combo = QComboBox()
        line_combo.addItems(line_names)
        layout.addWidget(line_combo)

        overhead_chk = QCheckBox("Add Overhead Wire")
        thirdrail_chk = QCheckBox("Add Third Rail")
        layout.addWidget(overhead_chk)
        layout.addWidget(thirdrail_chk)

        note = QLabel("Costs will be based on line length.")
        layout.addWidget(note)

        btn_layout = QHBoxLayout()
        ok = QPushButton("Upgrade")
        cancel = QPushButton("Cancel")
        btn_layout.addWidget(ok)
        btn_layout.addWidget(cancel)
        layout.addLayout(btn_layout)

        dlg.setLayout(layout)

        def do_upgrade():
            selected_line_name = line_combo.currentText()
            upgrades = []
            if overhead_chk.isChecked():
                upgrades.append('overhead')
            if thirdrail_chk.isChecked():
                upgrades.append('thirdrail')

            try:
                
                result = self.operations.upgrade_line_complete(selected_line_name, current_player, upgrades)
                self.check_and_eliminate_player(current_player)
                QMessageBox.information(self, "Upgraded", 
                    f"Upgraded {result['line_name']}. Cost: ${result['cost']}. Remaining money: ${result['remaining_money']}")
                dlg.close()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

        ok.clicked.connect(do_upgrade)
        cancel.clicked.connect(dlg.close)
        dlg.exec()

    def on_extend_line_clicked(self, preselected_line_name: str = None):
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        line_names = self.operations.get_player_line_names(current_player)
        if not line_names:
            QMessageBox.information(self, "Extend Line", "You have no lines to extend.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Extend Line")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Select Line to Extend:"))
        line_combo = QComboBox()
        line_combo.addItems(line_names)
        if preselected_line_name and preselected_line_name in line_names:
            index = line_names.index(preselected_line_name)
            line_combo.setCurrentIndex(index)
        layout.addWidget(line_combo)

        current_route_label = QLabel("Current route points:")
        layout.addWidget(current_route_label)
        route_display = QLabel("")
        route_display.setWordWrap(True)
        layout.addWidget(route_display)

        def update_route_display():
            selected_line_name = line_combo.currentText()
            line = self.operations.get_map_lines().get(selected_line_name)
            if line:
                route = self.operations.get_line_route(line)
                route_points = []
                for point in route:
                    # Handle both tuple/list and GridPoint objects
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        x, y = float(point[0]), float(point[1])
                    elif hasattr(point, 'x') and hasattr(point, 'y'):
                        x, y = float(point.x), float(point.y)
                    else:
                        continue
                    route_points.append(f"({x},{y})")
                route_text = ", ".join(route_points)
                route_display.setText(route_text)

        line_combo.currentIndexChanged.connect(update_route_display)
        update_route_display()

        layout.addWidget(QLabel("Extend from:"))
        direction_combo = QComboBox()
        direction_combo.addItems(["End of line", "Beginning of line"])
        layout.addWidget(direction_combo)

        layout.addWidget(QLabel("Enter new points (x, y) - one per line:"))
        layout.addWidget(QLabel("Example: 5,10 then 6,11"))
        from PyQt6.QtWidgets import QTextEdit
        points_text = QTextEdit()
        points_text.setPlaceholderText("5,10\n6,11\n7,12")
        points_text.setMinimumHeight(150)
        layout.addWidget(points_text)
        
        # Elevation selection will be done in a separate dialog after points are validated

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Extend")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        dlg.setLayout(layout)

        def do_extend():
            selected_line_name = line_combo.currentText()

            
            points_text_content = points_text.toPlainText().strip()
            if not points_text_content:
                QMessageBox.warning(dlg, "Error", "Please enter at least one point.")
                return

            new_points = []
            try:
                for line_text in points_text_content.split('\n'):
                    if line_text.strip():
                        x, y = map(float, line_text.strip().split(','))
                        new_points.append((x, y))
            except ValueError:
                QMessageBox.warning(dlg, "Error", "Invalid point format. Use 'x,y' format.")
                return

            if not new_points:
                QMessageBox.warning(dlg, "Error", "No valid points entered.")
                return

            extend_from_end = direction_combo.currentText() == "End of line"
            
            # Show elevation selection dialog
            required_elevations = len(new_points)
            elev_dlg = QDialog(self)
            elev_dlg.setWindowTitle("Select Elevation Levels")
            elev_dlg.setGeometry(300, 300, 400, min(600, 100 + required_elevations * 50))
            elev_layout = QVBoxLayout()
            elev_layout.addWidget(QLabel(f"Select elevation level for each of {required_elevations} points:"))
            elev_layout.addWidget(QLabel("Note: Cannot use 'street' on water"))
            
            from PyQt6.QtWidgets import QScrollArea, QWidget as QWidget2
            elev_scroll = QScrollArea()
            elev_scroll.setWidgetResizable(True)
            elev_widget = QWidget2()
            elev_widget_layout = QVBoxLayout()
            elev_widget.setLayout(elev_widget_layout)
            elev_scroll.setWidget(elev_widget)
            elev_layout.addWidget(elev_scroll)
            
            elevation_combos = []
            for i in range(required_elevations):
                point_label = QLabel(f"Point {i+1} ({new_points[i][0]:.1f}, {new_points[i][1]:.1f}):")
                elev_widget_layout.addWidget(point_label)
                combo = QComboBox()
                combo.addItems(['street', 'above_ground', 'underground'])
                combo.setCurrentText('street')  # Default
                elev_widget_layout.addWidget(combo)
                elevation_combos.append(combo)
            
            elev_btn_layout = QHBoxLayout()
            elev_ok_btn = QPushButton("OK")
            elev_cancel_btn = QPushButton("Cancel")
            elev_ok_btn.clicked.connect(elev_dlg.accept)
            elev_cancel_btn.clicked.connect(elev_dlg.reject)
            elev_btn_layout.addWidget(elev_ok_btn)
            elev_btn_layout.addWidget(elev_cancel_btn)
            elev_layout.addLayout(elev_btn_layout)
            elev_dlg.setLayout(elev_layout)
            
            if elev_dlg.exec() != QDialog.DialogCode.Accepted:
                return
            
            # Get elevation levels from combo boxes
            elevation_levels = [combo.currentText() for combo in elevation_combos]
            
            try:
                cost_info = self.operations.calculate_extend_cost(selected_line_name, new_points, extend_from_end, elevation_levels)
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
                return

            reply = QMessageBox.question(dlg, "Confirm Line Extension",
                                         f"Extending this line will cost ${cost_info['cost']} (distance: {cost_info['length']:.2f} units). Proceed?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

            try:
                
                result = self.operations.extend_line_complete(selected_line_name, current_player, new_points, extend_from_end, elevation_levels)
                self.check_and_eliminate_player(current_player)
                QMessageBox.information(dlg, "Line Extension Started", 
                    f"Extension of line '{result['line_name']}' has been started.\n"
                    f"Points to extend: {result['points_count']}\n"
                    f"Cost: ${result['cost']}\n"
                    f"Estimated build time: {result['build_time']} turn(s)\n"
                    f"Remaining money: ${result['remaining_money']}\n"
                    f"The extension will appear in the Construction tab.")
                dlg.close()
                self.refresh_construction_tab()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", f"Failed to extend line: {str(e)}")

        ok_btn.clicked.connect(do_extend)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_create_line_clicked(self):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        
        line_name, ok = QInputDialog.getText(self, "Create Line", "Enter line name:")
        if not ok or not line_name:
            return
        
        
        points_dialog = QDialog(self)
        points_dialog.setWindowTitle(f"Define Route for {line_name}")
        points_dialog.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Enter route points (x, y) - one per line."))
        layout.addWidget(QLabel("Example: 0,0 then 1,1 then 2,2"))
        
        from PyQt6.QtWidgets import QTextEdit
        points_text = QTextEdit()
        points_text.setPlaceholderText("0,0\n1,1\n2,1")
        points_text.setMinimumHeight(200)
        layout.addWidget(points_text)
        
        # Elevation selection will be done in a separate dialog after points are validated
        
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("Create Line")
        cancel_btn = QPushButton("Cancel")
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        points_dialog.setLayout(layout)
        
        def on_create_line():
            
            points_text_content = points_text.toPlainText().strip()
            if not points_text_content:
                QMessageBox.warning(self, "Error", "Please enter at least one point")
                return
            
            points = []
            try:
                for line in points_text_content.split('\n'):
                    if line.strip():
                        x, y = map(float, line.strip().split(','))
                        points.append((x, y))
            except ValueError:
                QMessageBox.warning(self, "Error", "Invalid point format. Use 'x,y' format.")
                return
            
            if len(points) < 2:
                QMessageBox.warning(self, "Error", "A line must have at least 2 points")
                return

            # Show elevation selection dialog
            required_elevations = len(points) - 1
            elev_dlg = QDialog(self)
            elev_dlg.setWindowTitle("Select Elevation Levels")
            elev_dlg.setGeometry(300, 300, 400, min(600, 100 + required_elevations * 50))
            elev_layout = QVBoxLayout()
            elev_layout.addWidget(QLabel(f"Select elevation level for each of {required_elevations} segments:"))
            elev_layout.addWidget(QLabel("Note: Cannot use 'street' on water"))
            
            from PyQt6.QtWidgets import QScrollArea, QWidget as QWidget2
            elev_scroll = QScrollArea()
            elev_scroll.setWidgetResizable(True)
            elev_widget = QWidget2()
            elev_widget_layout = QVBoxLayout()
            elev_widget.setLayout(elev_widget_layout)
            elev_scroll.setWidget(elev_widget)
            elev_layout.addWidget(elev_scroll)
            
            elevation_combos = []
            for i in range(required_elevations):
                segment_label = QLabel(f"Segment {i+1} (point {i} → {i+1}):")
                elev_widget_layout.addWidget(segment_label)
                combo = QComboBox()
                combo.addItems(['street', 'above_ground', 'underground'])
                combo.setCurrentText('street')  # Default
                elev_widget_layout.addWidget(combo)
                elevation_combos.append(combo)
            
            elev_btn_layout = QHBoxLayout()
            elev_ok_btn = QPushButton("OK")
            elev_cancel_btn = QPushButton("Cancel")
            elev_ok_btn.clicked.connect(elev_dlg.accept)
            elev_cancel_btn.clicked.connect(elev_dlg.reject)
            elev_btn_layout.addWidget(elev_ok_btn)
            elev_btn_layout.addWidget(elev_cancel_btn)
            elev_layout.addLayout(elev_btn_layout)
            elev_dlg.setLayout(elev_layout)
            
            if elev_dlg.exec() != QDialog.DialogCode.Accepted:
                return
            
            # Get elevation levels from combo boxes
            elevation_levels = [combo.currentText() for combo in elevation_combos]

            try:
                cost_info = self.operations.calculate_line_cost(points, elevation_levels)
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
                return

            reply = QMessageBox.question(self, "Confirm Line Creation",
                                         f"Creating this line will cost ${cost_info['cost']} (distance: {cost_info['length']:.2f} units). Proceed?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

            try:
                
                result = self.operations.create_line_complete(line_name, points, current_player, elevation_levels)
                self.check_and_eliminate_player(current_player)
                QMessageBox.information(self, "Line Construction Started", 
                    f"Line '{line_name}' construction started with {result['points_count']} points!\n"
                    f"Cost: ${result['cost']}\nBuild Time: {result['build_time']} turns\nRemaining Money: ${result['remaining_money']}")
                points_dialog.close()
                
                self.refresh_construction_tab()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to create line: {str(e)}")
        
        ok_btn.clicked.connect(on_create_line)
        cancel_btn.clicked.connect(points_dialog.close)
        
        points_dialog.exec()


    def add_year_display_to_tab(self, layout: QVBoxLayout, include_end_year_button: bool = True):
        
        year_widget_layout = QHBoxLayout()
        year_widget_layout.addStretch()
        
        if self.operations is not None:
            try:
                year_text = f"Year: {self.operations.get_year()}"
            except Exception:
                year_text = "Year: 1830"
        else:
            year_text = "Year: 1830"
        
        year_label = QLabel(year_text)
        year_label.setStyleSheet("font-weight: bold; font-size: 12pt; padding: 5px;")
        year_widget_layout.addWidget(year_label)
        
        if include_end_year_button:
            advance_year_btn = QPushButton("End Year")
            advance_year_btn.clicked.connect(self.advance_year_button_clicked)
            advance_year_btn.setMinimumWidth(100)
            advance_year_btn.setMinimumHeight(30)
            year_widget_layout.addWidget(advance_year_btn)
        
        layout.insertLayout(0, year_widget_layout)
    
    def AdvanceYearButton(self):
        pass

    def update_status_bar(self):
        if self.operations is None or self.status_bar is None:
            return
        try:
            current_year = self.operations.get_year()
            if self.status_year_label:
                self.status_year_label.setText(f"Year: {current_year}")
            active_players = self.operations.get_active_players()
            for i, player in enumerate(active_players):
                if i in self.status_money_labels:
                    money = self.operations.get_player_money(player)
                    player_name = self.operations.get_player_name(player)
                    self.status_money_labels[i].setText(f"{player_name}: ${money}")
            for i in range(len(active_players), len(self.status_money_labels)):
                if i in self.status_money_labels:
                    self.status_money_labels[i].setText("")
        except Exception:
            pass

    def advance_year_button_clicked(self):
        from PyQt6.QtWidgets import QMessageBox
        
        old_year = self.operations.get_year()
        
        try:
            result = self.operations.advance_year_complete(old_year)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Error during year advance: {str(e)}"
            )
            return
        
        new_year = result['new_year']
        
        self.DisplayMapInfo()
        
        if self.status_year_label:
            self.status_year_label.setText(f"Year: {new_year}")
        
        
        if result['strike_warnings']:
            strike_warnings = [w['message'] for w in result['strike_warnings']]
            QMessageBox.warning(
                self,
                "Strike Warning",
                "⚠️ WORKERS THREATENING TO STRIKE ⚠️\n\n" + "\n\n".join(strike_warnings)
            )
        
        
        if result.get('natural_disaster_info'):
            disaster_info = result['natural_disaster_info']
            msg = f"⚠️ NATURAL DISASTER: {disaster_info['name']} ⚠️\n\n{disaster_info['description']}"
            msg += f"\n\nEffects:"
            msg += f"\n- {disaster_info['damage_percent']:.0f}% of infrastructure damaged"
            msg += f"\n- {disaster_info['profit_reduction']:.0f}% profit reduction for {disaster_info['recovery_years']} year(s)"
            
            
            damaged_lines = disaster_info.get('damaged_lines', [])
            damaged_stations = disaster_info.get('damaged_stations', [])
            if damaged_lines:
                msg += f"\n\nDamaged Lines: {', '.join(damaged_lines[:5])}"
                if len(damaged_lines) > 5:
                    msg += f" (and {len(damaged_lines) - 5} more)"
            if damaged_stations:
                msg += f"\nDamaged Stations: {len(damaged_stations)} station(s)"
            
            QMessageBox.warning(self, "Natural Disaster", msg)

        
        if result['lawsuit_result']:
            lawsuit = result['lawsuit_result']
            if lawsuit['agency_won']:
                QMessageBox.warning(
                    self,
                    "Agency Lawsuit",
                    f"{lawsuit['agency']} sued {lawsuit['player']} for {lawsuit['excuse']}!\n"
                    f"The agency won. {lawsuit['player']} lost ${lawsuit['penalty']}."
                )
            else:
                QMessageBox.information(
                    self,
                    "Agency Lawsuit",
                    f"{lawsuit['agency']} sued {lawsuit['player']} for {lawsuit['excuse']}!\n"
                    f"{lawsuit['player']} won the lawsuit."
                )
        
        
        if result['newly_triggered_events']:
            msg = "Historical Event!\n\n"
            for event in result['newly_triggered_events']:
                msg += f"• {event.name} ({event.year})\n{event.description}\n\n"
            QMessageBox.information(self, "Historical Event", msg)

        
        if result['newly_available_vehicles']:
            msg = "New vehicle technology now available!\n\n"
            for name, year in sorted(result['newly_available_vehicles'], key=lambda x: x[1]):
                msg += f"• {name} (discovered {year})\n"
            QMessageBox.information(self, "Technology Breakthrough", msg)
        
        
        from worker import LAWYER
        import random
        if self.last_lawyer_replenishment_year is None:
            self.last_lawyer_replenishment_year = old_year - random.randint(0, 4)
        years_since_last_lawyer = new_year - self.last_lawyer_replenishment_year
        if years_since_last_lawyer >= 5:
            lawyer_replenishment = random.randint(1, 3)
            self.operations.marketplace_add_worker_supply(LAWYER.name, lawyer_replenishment)
            self.last_lawyer_replenishment_year = new_year
        
        
        self.DisplayMarketplaceInfo()
        players = self.operations.get_players()
        for player in players:
            self.DisplayRollingStockInfo(player)
        self.update_status_bar()

        
        profit_info = "\n---Profit---\n"
        maintenance_info = "\n---Trainset Maintenance---\n"
        resource_costs_info = "\n---Resource Costs---\n"
        line_maintenance_info = "\n---Line Maintenance---\n"
        tax_info = "\n---Taxes---\n"
        net_info = "\n---Net Change---\n"

        for pf in result['player_finances']:
            player = pf['player']
            finances = pf['finances']
            
            if finances['profit']:
                profit_info += f"{self.operations.get_player_name(player)}: ${finances['profit']}\n"
            if finances['trainset_maintenance']:
                maintenance_info += f"{self.operations.get_player_name(player)}: ${finances['trainset_maintenance']}\n"
            if finances['resource_charge']:
                resource_costs_info += f"{self.operations.get_player_name(player)}: ${finances['resource_charge']}\n"
            if finances['line_maintenance']:
                line_maintenance_info += f"{self.operations.get_player_name(player)}: ${finances['line_maintenance']}\n"
            if finances['worker_costs'] > 0:
                maintenance_info += f"{self.operations.get_player_name(player)}: Worker costs=${finances['worker_costs']}\n"
            tax_info += f"{self.operations.get_player_name(player)}: poll=${finances['poll_tax']}, property=${finances['property_tax']}\n"
            net_info += f"{self.operations.get_player_name(player)}: ${finances['net']}\n"

            
            if not self.operations.is_player_eliminated(player):
                self.check_and_eliminate_player(player)
                
        
        strike_info = ""
        if result['strike_messages']:
            from worker import ENGINEER
            strike_lines = []
            for sm in result['strike_messages']:
                strike_lines.append(f"{sm['player_name']}: {sm['quantity']} {sm['worker_name']}s on strike for {sm['turns_remaining']} more turn(s)! (Demanding {sm['demanded_multiplier']:.2f}x salary)")
            strike_info = "\n---Strikes---\n" + "\n".join(strike_lines) + "\n"
        
        
        info = "\n".join([f"{self.operations.get_player_name(p)}: Money: ${self.operations.get_player_money(p)}" for p in result['active_players']])
        
        summary = profit_info + maintenance_info + resource_costs_info + line_maintenance_info + tax_info + net_info + strike_info + "\n" + info
        QMessageBox.information(self, "End of Year Summary", summary)
        
        
        self.refresh_construction_tab()
        
        
        try:
            self._auto_save_game(new_year)
        except Exception as e:
            print(f"Auto-save failed: {e}")
    
    def _auto_save_game(self, current_year: int):
        
        if not self.operations:
            return
        
        
        save_dir = SAVEGAMES_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        
        
        save_name = f"autosave_{current_year}"
        save_path = save_dir / f"{save_name}.pkl"
        
        try:
            
            game = self.operations.get_game_for_save()
            
            
            save_data = {
                'game': game,
                'current_player_index': self.current_player_index,
            }
            
            
            with open(save_path, 'wb') as f:
                pickle.dump(save_data, f)
        except Exception as e:
            
            print(f"Auto-save failed: {e}")
    
    def on_save_game_clicked(self):
        
        if not self.operations:
            QMessageBox.warning(self, "No Game", "No game is currently running.")
            return
        
        
        save_name, ok = QInputDialog.getText(self, "Save Game", "Enter save game name:")
        if not ok or not save_name:
            return
        
        
        save_name = "".join(c for c in save_name if c.isalnum() or c in (' ', '-', '_')).strip()
        if not save_name:
            QMessageBox.warning(self, "Invalid Name", "Save game name cannot be empty.")
            return
        
        
        save_dir = SAVEGAMES_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"{save_name}.pkl"
        
        try:
            game = self.operations.get_game_for_save()
            
            
            save_data = {
                'game': game,
                'current_player_index': self.current_player_index,
            }
            
            
            with open(save_path, 'wb') as f:
                pickle.dump(save_data, f)
            
            QMessageBox.information(self, "Game Saved", f"Game saved successfully as '{save_name}'.")
            return True
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Failed to save game: {str(e)}")
            return False
    
    def on_end_game_clicked(self):
        
        if not self.operations:
            
            self._return_to_game_tab()
            return
        
        
        reply = QMessageBox.question(self, "End Game", 
            "Would you like to save the game before ending?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
        
        if reply == QMessageBox.StandardButton.Cancel:
            return  
        
        if reply == QMessageBox.StandardButton.Yes:
            
            saved = self.on_save_game_clicked()
            if not saved:
                
                confirm = QMessageBox.question(self, "Confirm End Game",
                    "Save was cancelled or failed. Do you still want to end the game?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if confirm == QMessageBox.StandardButton.No:
                    return
        
        
        self._return_to_game_tab()
    
    def _return_to_game_tab(self):
        
        
        while self.tabs.count() > 0:
            self.tabs.removeTab(0)
        
        
        self.game_tab = QWidget()
        self.game_layout = QVBoxLayout()
        self.game_tab.setLayout(self.game_layout)
        self.tabs.addTab(self.game_tab, "Game")
        
        
        self.layout = self.game_layout
        
        
        self.SetupGameTab()
        
        
        if self.operations is not None:
            del self.operations
        self.operations = None
        
        
        self.current_player_index = 0
        
        
        self.info_tab = None
        self.info_layout = None
        self.marketplace_tab = None
        self.marketplace_layout = None
    
    def on_load_game_clicked(self):
        
        
        save_dir = SAVEGAMES_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        
        
        save_files = list(save_dir.glob("*.pkl"))
        if not save_files:
            QMessageBox.information(self, "No Saves", "No save games found.")
            return
        
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Load Game")
        dlg.setGeometry(200, 200, 400, 300)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select save game to load:"))
        save_combo = QComboBox()
        
        for save_file in sorted(save_files):
            save_combo.addItem(save_file.stem, save_file)
        layout.addWidget(save_combo)
        
        btn_layout = QHBoxLayout()
        load_btn = QPushButton("Load")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(load_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_load():
            selected_file = save_combo.currentData()
            if not selected_file:
                QMessageBox.warning(dlg, "Error", "Please select a save game.")
                return
            
            try:
                
                with open(selected_file, 'rb') as f:
                    save_data = pickle.load(f)
                
                game = save_data['game']
                current_player_index = save_data.get('current_player_index', 0)
                
                
                self.operations = Operations([])
                self.operations.set_game_from_load(game)
                self.current_player_index = current_player_index
                self.operations.set_current_player_index(current_player_index)
                
                if self.status_bar is None:
                    central_layout = self.centralWidget().layout()
                    self.status_bar = QWidget()
                    self.status_bar_layout = QHBoxLayout()
                    self.status_bar.setLayout(self.status_bar_layout)
                    self.status_year_label = QLabel("Year: 1830")
                    self.status_end_year_btn = QPushButton("End Year")
                    self.status_end_year_btn.clicked.connect(self.advance_year_button_clicked)
                    self.status_bar_layout.addWidget(self.status_year_label)
                    self.status_bar_layout.addStretch()
                    for i in range(8):
                        money_label = QLabel("")
                        self.status_money_labels[i] = money_label
                        self.status_bar_layout.addWidget(money_label)
                    self.status_bar_layout.addWidget(self.status_end_year_btn)
                    central_layout.addWidget(self.status_bar)
                
                try:
                    self.tabs.removeTab(0)
                except Exception:
                    pass
                
                self._setup_game_ui()
                # Enable save game and game menu actions now that game is running
                if hasattr(self, 'save_game_action'):
                    self.save_game_action.setEnabled(True)
                if hasattr(self, 'advance_year_action'):
                    self.advance_year_action.setEnabled(True)
                if hasattr(self, 'refresh_displays_action'):
                    self.refresh_displays_action.setEnabled(True)
                
                QMessageBox.information(dlg, "Game Loaded", f"Game '{save_combo.currentText()}' loaded successfully.")
                dlg.close()
            except Exception as e:
                QMessageBox.warning(dlg, "Load Error", f"Failed to load game: {str(e)}")
        
        load_btn.clicked.connect(do_load)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()
    
    def _setup_game_ui(self):
        
        
        players = self.operations.get_players()
        for player in players:
            player_id = self.operations.get_player_id(player)
            player_name = player.get_name()
            player_tabs = self._create_player_tabs(player)
            self.tabs.addTab(player_tabs, player_name)
        self.current_player_index = 0
        if players:
            first_player = players[0]
            first_player_id = self.operations.get_player_id(first_player)
            self.lines_tab = self.player_tabs_dict[first_player_id]['lines_tab']
            self.lines_layout = self.player_tabs_dict[first_player_id]['lines_layout']
            self.rolling_stock_tab = self.player_tabs_dict[first_player_id]['rolling_stock_tab']
            self.rolling_stock_tabs = self.player_tabs_dict[first_player_id]['rolling_stock_tabs']
            self.rolling_stock_trainsets_tab = self.player_tabs_dict[first_player_id]['rolling_stock_trainsets_tab']
            self.rolling_stock_trainsets_layout = self.player_tabs_dict[first_player_id]['rolling_stock_trainsets_layout']
            self.rolling_stock_locomotives_tab = self.player_tabs_dict[first_player_id]['rolling_stock_locomotives_tab']
            self.rolling_stock_locomotives_layout = self.player_tabs_dict[first_player_id]['rolling_stock_locomotives_layout']
            self.rolling_stock_railcars_tab = self.player_tabs_dict[first_player_id]['rolling_stock_railcars_tab']
            self.rolling_stock_railcars_layout = self.player_tabs_dict[first_player_id]['rolling_stock_railcars_layout']
            self.workers_tab = self.player_tabs_dict[first_player_id]['workers_tab']
            self.workers_layout = self.player_tabs_dict[first_player_id]['workers_layout']
            self.law_tab = self.player_tabs_dict[first_player_id]['law_tab']
            self.law_layout = self.player_tabs_dict[first_player_id]['law_layout']
            self.marketplace_tab = self.player_tabs_dict[first_player_id]['marketplace_tab']
            self.marketplace_main_layout = self.player_tabs_dict[first_player_id]['marketplace_main_layout']
            self.marketplace_tabs = self.player_tabs_dict[first_player_id]['marketplace_tabs']
            self.marketplace_lines_tab = self.player_tabs_dict[first_player_id]['marketplace_lines_tab']
            self.marketplace_lines_layout = self.player_tabs_dict[first_player_id]['marketplace_lines_layout']
            self.marketplace_trainsets_tab = self.player_tabs_dict[first_player_id]['marketplace_trainsets_tab']
            self.marketplace_trainsets_layout = self.player_tabs_dict[first_player_id]['marketplace_trainsets_layout']
            self.marketplace_locomotives_tab = self.player_tabs_dict[first_player_id]['marketplace_locomotives_tab']
            self.marketplace_locomotives_layout = self.player_tabs_dict[first_player_id]['marketplace_locomotives_layout']
            self.marketplace_ferries_tab = self.player_tabs_dict[first_player_id]['marketplace_ferries_tab']
            self.marketplace_ferries_layout = self.player_tabs_dict[first_player_id]['marketplace_ferries_layout']
            self.marketplace_horses_trolleys_tab = self.player_tabs_dict[first_player_id]['marketplace_horses_trolleys_tab']
            self.marketplace_horses_trolleys_layout = self.player_tabs_dict[first_player_id]['marketplace_horses_trolleys_layout']
            self.marketplace_railcars_tab = self.player_tabs_dict[first_player_id]['marketplace_railcars_tab']
            self.marketplace_railcars_layout = self.player_tabs_dict[first_player_id]['marketplace_railcars_layout']
            self.marketplace_workers_tab = self.player_tabs_dict[first_player_id]['marketplace_workers_tab']
            self.marketplace_workers_layout = self.player_tabs_dict[first_player_id]['marketplace_workers_layout']
            self.marketplace_layout = self.marketplace_lines_layout
            self.map_tab = self.player_tabs_dict[first_player_id]['map_tab']
            self.map_layout = self.player_tabs_dict[first_player_id]['map_layout']
            self.construction_tab = self.player_tabs_dict[first_player_id]['construction_tab']
            self.construction_layout = self.player_tabs_dict[first_player_id]['construction_layout']
            self.construction_subtabs = self.player_tabs_dict[first_player_id]['construction_subtabs']
            self.in_progress_layout = self.player_tabs_dict[first_player_id]['in_progress_layout']
            self.complete_jobs_layout = self.player_tabs_dict[first_player_id]['complete_jobs_layout']
        if players:
            for player in players:
                self.DisplayRollingStockInfo(player)
                player_id = self.operations.get_player_id(player)
                if player_id in self.player_tabs_dict:
                    self.DisplayMarketplaceInfo()
                    self.DisplayMapInfo()
        self.CreateLineButton()
        self.UpgradeLineButton()
        self.ExtendLineButton()
        if players:
            for player in players:
                self.DisplayLinesControls(player)
                self.DisplayWorkersControls(player)
                try:
                    self.DisplayLawInfo(player)
                except Exception as e:
                    player_id = self.operations.get_player_id(player)
                    if player_id in self.player_tabs_dict:
                        self.player_tabs_dict[player_id]['law_layout'].addWidget(QLabel(f"Error loading lawsuit info: {str(e)}"))
        if players:
            for player in players:
                self.refresh_construction_tab()
        self.update_status_bar()

    def CreateTrainsetButton(self, layout=None):
        self.create_trainset_button = QPushButton("Create Trainset")
        self.create_trainset_button.clicked.connect(self.on_create_trainset_clicked)
        if layout is not None:
            layout.addWidget(self.create_trainset_button)
        elif hasattr(self, 'rolling_stock_trainsets_layout') and self.rolling_stock_trainsets_layout is not None:
            self.rolling_stock_trainsets_layout.addWidget(self.create_trainset_button)
    
    def on_create_trainset_clicked(self):
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        locomotives = getattr(current_player, 'locomotives', [])
        railcars = getattr(current_player, 'railcars', [])
        if not locomotives:
            QMessageBox.information(self, "Create Trainset", "You have no locomotives. Buy one from the marketplace first.")
            return
        if not railcars:
            QMessageBox.information(self, "Create Trainset", "You have no railcars. Buy some from the marketplace first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Create Trainset")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Select Locomotive:"))
        loco_combo = QComboBox()
        for loco in locomotives:
            loco_name = getattr(loco, 'name', 'Unknown')
            loco_combo.addItem(loco_name, loco)
        layout.addWidget(loco_combo)
        layout.addWidget(QLabel("Select Railcars (hold Ctrl to select multiple):"))
        railcar_list = QListWidget()
        railcar_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for car in railcars:
            car_name = getattr(car, 'name', 'Unknown')
            cap = getattr(car, 'capacity', 0)
            railcar_list.addItem(f"{car_name} (Capacity: {cap})")
        layout.addWidget(railcar_list)
        btn_layout = QHBoxLayout()
        create_btn = QPushButton("Create")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)
        def do_create():
            selected_loco = loco_combo.currentData()
            if not selected_loco:
                QMessageBox.warning(dlg, "Error", "Please select a locomotive.")
                return
            
            can_have_cars = getattr(selected_loco, 'can_have_railcars', True)
            selected_items = railcar_list.selectedItems()
            
            if can_have_cars:
                if not selected_items:
                    QMessageBox.warning(dlg, "Error", "Please select at least one railcar.")
                    return
                selected_cars = [railcars[railcar_list.row(item)] for item in selected_items]
            else:
                if selected_items:
                    QMessageBox.warning(dlg, "Error", f"{selected_loco.name} is a self-contained unit and cannot have railcars attached.")
                    return
                selected_cars = []
            
            try:
                trainset = self.operations.create_trainset(selected_loco, selected_cars)
                self.operations.acquire_trainset_for_player(current_player, trainset)
                for car in selected_cars:
                    if car in railcars:
                        railcars.remove(car)
                if selected_loco in locomotives:
                    locomotives.remove(selected_loco)
                QMessageBox.information(dlg, "Trainset Created", "Trainset created successfully!")
                dlg.close()
                self.DisplayRollingStockInfo(current_player)
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        create_btn.clicked.connect(do_create)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def AddRailcarButton(self, layout=None):
        self.add_railcar_button = QPushButton("Add Railcar to Trainset")
        self.add_railcar_button.clicked.connect(self.on_add_railcar_clicked)
        if layout is not None:
            layout.addWidget(self.add_railcar_button)
        elif hasattr(self, 'rolling_stock_trainsets_layout') and self.rolling_stock_trainsets_layout is not None:
            self.rolling_stock_trainsets_layout.addWidget(self.add_railcar_button)
    
    def SellTrainsetButton(self, layout=None):
        self.sell_trainset_button = QPushButton("Sell Trainset to Marketplace")
        self.sell_trainset_button.clicked.connect(self.on_sell_trainset_clicked)
        if layout is not None:
            layout.addWidget(self.sell_trainset_button)
        elif hasattr(self, 'rolling_stock_trainsets_layout') and self.rolling_stock_trainsets_layout is not None:
            self.rolling_stock_trainsets_layout.addWidget(self.sell_trainset_button)
    
    def SellToHistoricalSocietyButton(self, layout=None):
        self.sell_historical_button = QPushButton("Sell to Historical Society")
        self.sell_historical_button.clicked.connect(self.on_sell_to_historical_society_clicked)
        if layout is not None:
            layout.addWidget(self.sell_historical_button)
        elif hasattr(self, 'rolling_stock_trainsets_layout') and self.rolling_stock_trainsets_layout is not None:
            self.rolling_stock_trainsets_layout.addWidget(self.sell_historical_button)

    def AssembleTrainsetButton(self, layout=None):
        self.assemble_trainset_button = QPushButton("Assemble Trainset")
        self.assemble_trainset_button.clicked.connect(self.on_assemble_trainset_clicked)
        if layout is not None:
            layout.addWidget(self.assemble_trainset_button)
        elif hasattr(self, 'rolling_stock_trainsets_layout') and self.rolling_stock_trainsets_layout is not None:
            self.rolling_stock_trainsets_layout.addWidget(self.assemble_trainset_button)

    def AssignTrainsetButton(self, layout=None):
        self.assign_trainset_button = QPushButton("Assign Trainset to Line")
        self.assign_trainset_button.clicked.connect(self.on_assign_trainset_clicked)
        if layout is not None:
            layout.addWidget(self.assign_trainset_button)
        elif hasattr(self, 'lines_layout') and self.lines_layout:
            self.lines_layout.addWidget(self.assign_trainset_button)
        else:
            if hasattr(self, 'info_layout') and self.info_layout:
                self.info_layout.addWidget(self.assign_trainset_button)

    def UnassignTrainsetButton(self, layout=None):
        self.unassign_trainset_button = QPushButton("Unassign Trainset from Line")
        self.unassign_trainset_button.clicked.connect(self.on_unassign_trainset_clicked)
        if layout is not None:
            layout.addWidget(self.unassign_trainset_button)
        elif hasattr(self, 'lines_layout') and self.lines_layout:
            self.lines_layout.addWidget(self.unassign_trainset_button)
        else:
            if hasattr(self, 'info_layout') and self.info_layout:
                self.info_layout.addWidget(self.unassign_trainset_button)
    
    def DisassembleTrainsetButton(self, layout=None):
        self.disassemble_trainset_button = QPushButton("Disassemble Trainset")
        self.disassemble_trainset_button.clicked.connect(self.on_disassemble_trainset_clicked)
        if layout is not None:
            layout.addWidget(self.disassemble_trainset_button)
        elif hasattr(self, 'rolling_stock_trainsets_layout') and self.rolling_stock_trainsets_layout is not None:
            self.rolling_stock_trainsets_layout.addWidget(self.disassemble_trainset_button)

    def buy_trainset_from_marketplace(self, sale_item):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        current_year = self.operations.get_year()
        
        try:
            
            result = self.operations.buy_vehicle_complete(sale_item, current_player, current_year)
            self.check_and_eliminate_player(current_player)
            
            from vehicle import Trainset
            trainset = result['item']
            if isinstance(trainset, Trainset):
                quality_text = ""
                if hasattr(trainset, 'locomotive') and trainset.locomotive:
                    loco = trainset.locomotive
                    speed = getattr(loco, 'speed', 'N/A')
                    quality_text = f"Speed: {speed} mph\n"
                item_info = (
                    f"Trainset purchased!\n"
                    f"Locomotive: {result['item_name']}\n"
                    f"Capacity: {result['capacity']}\n"
                    f"{quality_text}"
                    f"Cost: ${result['price']}\n"
                    f"Remaining Money: ${result['remaining_money']}\n\n"
                    f"Note: Line quality (affecting ridership) depends on maintenance workers assigned to the line."
                )
                QMessageBox.information(self, "Trainset Purchased", item_info)
            
            
            self.DisplayMarketplaceInfo()
        except Exception as e:
            QMessageBox.warning(self, "Purchase Failed", str(e))
        
    
    def buy_vehicle_from_marketplace(self, sale_item):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        current_year = self.operations.get_year()
        
        try:
            
            result = self.operations.buy_vehicle_complete(sale_item, current_player, current_year)
            self.check_and_eliminate_player(current_player)
            
            from vehicle import Locomotive, Railcar
            item = result['item']
            from vehicle import Locomotive, Ferry, Bus, Railcar
            if isinstance(item, Locomotive):
                item_info = (
                    f"Locomotive purchased!\n"
                    f"Name: {result['item_name']}\n"
                    f"Cost: ${result['price']}\n"
                    f"Remaining Money: ${result['remaining_money']}\n\n"
                    f"You can assemble this locomotive with railcars to create a trainset."
                )
            elif isinstance(item, Ferry):
                item_info = (
                    f"Ferry purchased!\n"
                    f"Name: {result['item_name']}\n"
                    f"Cost: ${result['price']}\n"
                    f"Remaining Money: ${result['remaining_money']}\n\n"
                    f"Capacity: {result['capacity']} passengers"
                )
            elif isinstance(item, Bus):
                item_info = (
                    f"Bus purchased!\n"
                    f"Name: {result['item_name']}\n"
                    f"Cost: ${result['price']}\n"
                    f"Remaining Money: ${result['remaining_money']}\n\n"
                    f"Capacity: {result['capacity']} passengers"
                )
            elif isinstance(item, Railcar):
                item_info = (
                    f"Railcar purchased!\n"
                    f"Name: {result['item_name']}\n"
                    f"Capacity: {result['capacity']}\n"
                    f"Cost: ${result['price']}\n"
                    f"Remaining Money: ${result['remaining_money']}\n\n"
                    f"You can assemble this railcar with a locomotive to create a trainset."
                )
            else:
                item_info = f"Item purchased!\nCost: ${result['price']}\nRemaining Money: ${result['remaining_money']}"
            
            QMessageBox.information(self, "Item Purchased", item_info)
            
            self.DisplayMarketplaceInfo()
            
            self.DisplayRollingStockInfo(current_player)
            
            self.update_status_bar()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            if "Out of Stock" in str(e) or "no longer available" in str(e):
                self.DisplayMarketplaceInfo()
    
    def on_assemble_trainset_clicked(self):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        
        locomotives = getattr(current_player, 'locomotives', [])
        railcars = getattr(current_player, 'railcars', [])
        
        if not locomotives:
            QMessageBox.information(self, "Assemble Trainset", "You have no locomotives. Buy one from the marketplace first.")
            return
        
        if not railcars:
            QMessageBox.information(self, "Assemble Trainset", "You have no railcars. Buy some from the marketplace first.")
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Assemble Trainset")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select Locomotive:"))
        loco_combo = QComboBox()
        for i, loco in enumerate(locomotives):
            loco_name = getattr(loco, 'name', f'Locomotive {i}')
            can_have_cars = getattr(loco, 'can_have_railcars', True)
            if not can_have_cars:
                loco_name += " (self-contained, no railcars)"
            loco_combo.addItem(f"{loco_name}", loco)
        layout.addWidget(loco_combo)
        
        def update_railcar_visibility():
            selected_loco = loco_combo.currentData()
            can_have_cars = getattr(selected_loco, 'can_have_railcars', True) if selected_loco else True
            railcar_label.setVisible(can_have_cars)
            railcar_list.setVisible(can_have_cars)
            
            if not can_have_cars:
                info_label.setText(f"Note: {selected_loco.name} is a self-contained unit and cannot have railcars attached.")
            else:
                # Check if locomotive has an associated railcar requirement
                associated_railcar_name = getattr(selected_loco, 'associated_railcar_name', None)
                if associated_railcar_name:
                    info_label.setText(f"Note: {selected_loco.name} can only use '{associated_railcar_name}' railcars.")
                    # Filter railcar list to only show associated railcars
                    railcar_list.clear()
                    for i, car in enumerate(railcars):
                        if car.name == associated_railcar_name:
                            car_name = getattr(car, 'name', f'Railcar {i}')
                            capacity = getattr(car, 'capacity', 0)
                            railcar_list.addItem(f"{car_name} (cap: {capacity})")
                else:
                    info_label.setText("")
                    # Show all railcars
                    railcar_list.clear()
                    for i, car in enumerate(railcars):
                        car_name = getattr(car, 'name', f'Railcar {i}')
                        capacity = getattr(car, 'capacity', 0)
                        railcar_list.addItem(f"{car_name} (cap: {capacity})")
        
        info_label = QLabel("")
        info_label.setStyleSheet("color: blue; font-style: italic;")
        layout.addWidget(info_label)
        
        railcar_label = QLabel("Select Railcars (hold Ctrl to select multiple):")
        layout.addWidget(railcar_label)
        from PyQt6.QtWidgets import QListWidget
        railcar_list = QListWidget()
        railcar_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for i, car in enumerate(railcars):
            car_name = getattr(car, 'name', f'Railcar {i}')
            capacity = getattr(car, 'capacity', 0)
            railcar_list.addItem(f"{car_name} (cap: {capacity})")
        layout.addWidget(railcar_list)
        
        loco_combo.currentIndexChanged.connect(update_railcar_visibility)
        update_railcar_visibility()
        
        btn_layout = QHBoxLayout()
        assemble_btn = QPushButton("Assemble")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(assemble_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_assemble():
            selected_loco = loco_combo.currentData()
            if not selected_loco:
                QMessageBox.warning(dlg, "Error", "Please select a locomotive.")
                return
            
            can_have_cars = getattr(selected_loco, 'can_have_railcars', True)
            selected_indices = railcar_list.selectedIndexes()
            
            if can_have_cars:
                if not selected_indices:
                    QMessageBox.warning(dlg, "Error", "Please select at least one railcar.")
                    return
                selected_railcars = [railcars[idx.row()] for idx in selected_indices]
            else:
                if selected_indices:
                    QMessageBox.warning(dlg, "Error", f"{selected_loco.name} is a self-contained unit and cannot have railcars attached.")
                    return
                selected_railcars = []
            
            try:
                result = self.operations.assemble_trainset_complete(current_player, selected_loco, selected_railcars)
                QMessageBox.information(dlg, "Trainset Assembled", 
                    f"Trainset assembled!\nLocomotive: {result['locomotive_name']}\nRailcars: {result['railcars_count']}\nCapacity: {result['capacity']}")
                dlg.close()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        
        assemble_btn.clicked.connect(do_assemble)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_assign_trainset_clicked(self):
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        unassigned = self.operations.get_unassigned_trainsets(current_player)
        if not unassigned:
            QMessageBox.information(self, "Assign Trainset", "No unassigned trainsets available for this player.")
            return

        
        dlg = QDialog(self)
        dlg.setWindowTitle("Assign Trainset to Line")
        dlg.setGeometry(200, 200, 400, 200)
        l = QVBoxLayout()

        l.addWidget(QLabel("Select Trainset:"))
        ts_combo = QComboBox()
        ts_options = []
        for i, ts in enumerate(unassigned):
            loco_name = self.operations.get_trainset_locomotive_name(ts)
            opt = f"Trainset {i}: {loco_name} ({len(self.operations.get_trainset_cars(ts))} cars)"
            ts_options.append(opt)
        ts_combo.addItems(ts_options)
        l.addWidget(ts_combo)

        
        line_names = self.operations.get_player_line_names(current_player)
        if not line_names:
            QMessageBox.information(self, "Assign Trainset", "Player has no lines to assign to. Create a line first.")
            return
        l.addWidget(QLabel("Select Line:"))
        line_combo = QComboBox()
        line_combo.addItems(line_names)
        l.addWidget(line_combo)
        
        
        from worker import ENGINEER
        current_year = self.operations.get_year()
        engineer_count = self.operations.get_player_engineer_count(current_player)
        assigned_count = self.operations.get_player_assigned_trainsets_count(current_player)
        available_engineers = engineer_count - assigned_count
        
        engineer_info = QLabel(f"Available Engineers: {available_engineers} (Total: {engineer_count}, Assigned: {assigned_count})")
        if available_engineers <= 0:
            engineer_info.setStyleSheet("color: red; font-weight: bold;")
            engineer_info.setText(engineer_info.text() + " - WARNING: Not enough engineers!")
        l.addWidget(engineer_info)

        btn_layout = QHBoxLayout()
        ok = QPushButton("Assign")
        cancel = QPushButton("Cancel")
        btn_layout.addWidget(ok)
        btn_layout.addWidget(cancel)
        l.addLayout(btn_layout)
        dlg.setLayout(l)

        def do_assign():
            ts_idx = ts_combo.currentIndex()
            selected_ts = unassigned[ts_idx]
            selected_line = line_combo.currentText()
            
            
            engineer_count = self.operations.get_player_engineer_count(current_player)
            assigned_count = self.operations.get_player_assigned_trainsets_count(current_player)
            if assigned_count >= engineer_count:
                QMessageBox.warning(self, "Insufficient Engineers", 
                    f"You need at least {assigned_count + 1} engineers to assign this trainset.\n"
                    f"You currently have {engineer_count} engineers.\n"
                    f"Please rent more engineers from the Workers tab in the Marketplace.")
                return
            
            
            try:
                self.operations.assign_trainset_to_line(selected_ts, selected_line)
                QMessageBox.information(self, "Assigned", f"Trainset assigned to {selected_line}")
            except Exception as e:
                QMessageBox.warning(self, "Assignment Failed", str(e))
            dlg.close()

        ok.clicked.connect(do_assign)
        cancel.clicked.connect(dlg.close)
        dlg.exec()

    def on_edit_line_clicked(self, line, player):
        
        if not line or not player:
            QMessageBox.warning(self, "Edit Line", "Invalid line or player.")
            return
        current_name = self.operations.get_line_name(line)
        new_name, ok = QInputDialog.getText(self, "Edit Line", "Enter new line name:", text=current_name)
        if ok and new_name and new_name.strip():
            new_name = new_name.strip()
            if new_name != current_name:
                try:
                    self.operations.rename_line(line, new_name)
                    QMessageBox.information(self, "Line Renamed", f"Line renamed to '{new_name}'.")
                    self.DisplayLinesControls(player)
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to rename line: {str(e)}")
    
    def on_station_clicked(self, x: float, y: float, stations: list):
        if not stations:
            return
        
        if len(stations) == 1:
            station, line_name, line = stations[0]
            self.show_station_edit_dialog(station, line_name, line, x, y)
        else:
            dlg = QDialog(self)
            dlg.setWindowTitle("Select Station")
            dlg.setGeometry(200, 200, 400, 300)
            layout = QVBoxLayout()
            layout.addWidget(QLabel(f"Multiple stations at ({x}, {y}). Select one to edit:"))
            station_combo = QComboBox()
            for station, line_name, line in stations:
                station_combo.addItem(f"{station.name} ({line_name})", (station, line_name, line))
            layout.addWidget(station_combo)
            btn_layout = QHBoxLayout()
            ok_btn = QPushButton("Edit")
            cancel_btn = QPushButton("Cancel")
            btn_layout.addWidget(ok_btn)
            btn_layout.addWidget(cancel_btn)
            layout.addLayout(btn_layout)
            dlg.setLayout(layout)
            
            def do_edit():
                station, line_name, line = station_combo.currentData()
                dlg.close()
                self.show_station_edit_dialog(station, line_name, line, x, y)
            
            ok_btn.clicked.connect(do_edit)
            cancel_btn.clicked.connect(dlg.close)
            dlg.exec()
    
    def on_line_clicked(self, x: float, y: float, lines: list):
        if not lines:
            return
        
        # Check if we're in station creation mode (check Python-side state)
        current_player = self.operations.get_current_player(self.current_player_index)
        if current_player:
            current_player_id = self.operations.get_player_id(current_player)
            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
            if current_player_tabs_dict:
                # Check Python-side station creation state
                station_creation_state = getattr(self, '_station_creation_state', {}).get(current_player_id)
                if station_creation_state and station_creation_state.get('active', False):
                    selected_line = station_creation_state.get('line_name')
                    station_name = station_creation_state.get('station_name', '').strip()
                    
                    if selected_line and station_name:
                        # Find matching line - check if any of the clicked lines match
                        matching_line = None
                        for line, check_line_name, owner in lines:
                            if check_line_name == selected_line:
                                matching_line = (line, check_line_name, owner)
                                break
                        
                        if matching_line:
                            line, check_line_name, owner = matching_line
                            grid_x, grid_y = int(round(x)), int(round(y))
                            cost = self.operations.calculate_station_cost(1)
                            reply = QMessageBox.question(
                                self,
                                "Confirm Add Station",
                                f"Build station '{station_name}' at the clicked location ({grid_x}, {grid_y}) on line '{selected_line}'?\n\n"
                                f"Cost: ${cost}. The station will be added to the construction tab.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No
                            )
                            if reply != QMessageBox.StandardButton.Yes:
                                return
                            try:
                                result = self.operations.create_station_complete(station_name, grid_x, grid_y, current_player, [selected_line])
                                QMessageBox.information(self, "Station Job Created",
                                    f"Station '{station_name}' has been added to construction on line '{selected_line}' at ({grid_x}, {grid_y}).\n"
                                    f"Cost: ${result['cost']}, Build time: {result['build_time']} turns.")
                                
                                # Clear station creation state
                                if not hasattr(self, '_station_creation_state'):
                                    self._station_creation_state = {}
                                self._station_creation_state[current_player_id] = {'active': False}
                                
                                # Clear JavaScript state - with safety checks to prevent segfault
                                web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
                                if not web_view:
                                    web_view = getattr(self, 'map_web_view', None)
                                if web_view:
                                    try:
                                        # Check if web_view is still valid before accessing page
                                        page = self._safe_get_webview_page(web_view)
                                        if page:
                                            page.runJavaScript("window.pendingStationLine = null; window.pendingAddStation = null; window.isCreatingStation = false;")
                                    except (RuntimeError, AttributeError) as e:
                                        # Web view or page has been deleted, ignore
                                        print(f"    - Warning: Could not clear JavaScript state: {e}")
                                
                                # Clear input
                                station_name_input = current_player_tabs_dict.get('station_name_input')
                                if station_name_input:
                                    station_name_input.clear()
                                
                                # Refresh displays
                                self.DisplayMapInfo()
                                self.refresh_construction_tab()
                            except Exception as e:
                                QMessageBox.warning(self, "Error", f"Failed to create station job: {str(e)}")
                                # Don't clear state on error - allow user to try again
                            return  # Always return if in station creation mode, even on error
                        else:
                            # Line clicked doesn't match selected line - show warning
                            QMessageBox.warning(self, "Wrong Line", 
                                f"You selected line '{selected_line}' but clicked on a different line. Please click on '{selected_line}' to place the station.")
                            return  # Return to prevent normal line click handling
                    elif not station_name:
                        QMessageBox.warning(self, "Error", "Please enter a station name first.")
                        return  # Return early - don't show line dialog
                    else:
                        # Station creation active but no line selected - shouldn't happen, but return anyway
                        return
        
        if len(lines) == 1:
            line, line_name, owner = lines[0]
            self.show_line_dialog(line, line_name, owner, x, y)
        else:
            dlg = QDialog(self)
            dlg.setWindowTitle("Select Line")
            dlg.setGeometry(200, 200, 400, 300)
            layout = QVBoxLayout()
            layout.addWidget(QLabel(f"Multiple lines at ({x}, {y}). Select one to view:"))
            line_combo = QComboBox()
            for line, line_name, owner in lines:
                owner_name = owner.get_name() if owner else "None"
                line_combo.addItem(f"{line_name} (Owner: {owner_name})", (line, line_name, owner))
            layout.addWidget(line_combo)
            btn_layout = QHBoxLayout()
            ok_btn = QPushButton("View")
            cancel_btn = QPushButton("Cancel")
            btn_layout.addWidget(ok_btn)
            btn_layout.addWidget(cancel_btn)
            layout.addLayout(btn_layout)
            dlg.setLayout(layout)
            
            def do_view():
                line, line_name, owner = line_combo.currentData()
                dlg.close()
                self.show_line_dialog(line, line_name, owner, x, y)
            
            ok_btn.clicked.connect(do_view)
            cancel_btn.clicked.connect(dlg.close)
            dlg.exec()
    
    def show_line_dialog(self, line, line_name: str, owner, x: float, y: float):
        current_player = self.operations.get_current_player(self.current_player_index)
        is_owner = current_player and owner and self.operations.get_player_id(owner) == self.operations.get_player_id(current_player)
        
        stats = self.operations.get_line_stats(line)
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Line Information" if not is_owner else "Edit Line")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel(f"Line: {stats['name']}"))
        layout.addWidget(QLabel(f"Owner: {stats['owner_name']}"))
        layout.addWidget(QLabel(f"Route Length: {stats['route_length']} segments"))
        layout.addWidget(QLabel(f"Stations: {stats['num_stations']}"))
        layout.addWidget(QLabel(f"Vehicles: {stats['num_vehicles']}"))
        layout.addWidget(QLabel(f"Fare: ${stats['fare']}"))
        layout.addWidget(QLabel(f"Profit (this year): ${stats['profit']}"))
        layout.addWidget(QLabel(f"Maintenance: ${stats['maintenance']}"))
        
        if is_owner:
            name_layout = QHBoxLayout()
            name_layout.addWidget(QLabel("Line Name:"))
            name_edit = QLineEdit(stats['name'])
            name_layout.addWidget(name_edit)
            layout.addLayout(name_layout)
            
            extend_layout = QVBoxLayout()
            extend_layout.addWidget(QLabel("Extend Line:"))
            direction_combo = QComboBox()
            direction_combo.addItems(["End of line", "Beginning of line"])
            extend_layout.addWidget(direction_combo)
            
            from PyQt6.QtWidgets import QTextEdit
            points_text = QTextEdit()
            points_text.setPlaceholderText("Enter new points (x, y) - one per line:\nExample: 5,10\n6,11")
            points_text.setMinimumHeight(100)
            extend_layout.addWidget(points_text)
            
            # Elevation selection will be done in a separate dialog after points are validated
            
            extend_btn = QPushButton("Extend Line")
            extend_layout.addWidget(extend_btn)
            layout.addLayout(extend_layout)
            
            def do_extend():
                points_text_content = points_text.toPlainText().strip()
                if not points_text_content:
                    QMessageBox.warning(dlg, "Error", "Please enter at least one point.")
                    return
                
                new_points = []
                try:
                    for line_text in points_text_content.split('\n'):
                        if line_text.strip():
                            x, y = map(float, line_text.strip().split(','))
                            new_points.append((x, y))
                except ValueError:
                    QMessageBox.warning(dlg, "Error", "Invalid point format. Use 'x,y' format.")
                    return
                
                if not new_points:
                    QMessageBox.warning(dlg, "Error", "No valid points entered.")
                    return
                
                extend_from_end = direction_combo.currentText() == "End of line"
                
                # Show elevation selection dialog
                required_elevations = len(new_points)
                elev_dlg = QDialog(self)
                elev_dlg.setWindowTitle("Select Elevation Levels")
                elev_dlg.setGeometry(300, 300, 400, min(600, 100 + required_elevations * 50))
                elev_layout = QVBoxLayout()
                elev_layout.addWidget(QLabel(f"Select elevation level for each of {required_elevations} points:"))
                elev_layout.addWidget(QLabel("Note: Cannot use 'street' on water"))
                
                from PyQt6.QtWidgets import QScrollArea, QWidget as QWidget2
                elev_scroll = QScrollArea()
                elev_scroll.setWidgetResizable(True)
                elev_widget = QWidget2()
                elev_widget_layout = QVBoxLayout()
                elev_widget.setLayout(elev_widget_layout)
                elev_scroll.setWidget(elev_widget)
                elev_layout.addWidget(elev_scroll)
                
                elevation_combos = []
                for i in range(required_elevations):
                    point_label = QLabel(f"Point {i+1} ({new_points[i][0]:.1f}, {new_points[i][1]:.1f}):")
                    elev_widget_layout.addWidget(point_label)
                    combo = QComboBox()
                    combo.addItems(['street', 'above_ground', 'underground'])
                    combo.setCurrentText('street')  # Default
                    elev_widget_layout.addWidget(combo)
                    elevation_combos.append(combo)
                
                elev_btn_layout = QHBoxLayout()
                elev_ok_btn = QPushButton("OK")
                elev_cancel_btn = QPushButton("Cancel")
                elev_ok_btn.clicked.connect(elev_dlg.accept)
                elev_cancel_btn.clicked.connect(elev_dlg.reject)
                elev_btn_layout.addWidget(elev_ok_btn)
                elev_btn_layout.addWidget(elev_cancel_btn)
                elev_layout.addLayout(elev_btn_layout)
                elev_dlg.setLayout(elev_layout)
                
                if elev_dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                
                # Get elevation levels from combo boxes
                elevation_levels = [combo.currentText() for combo in elevation_combos]
                
                try:
                    cost_info = self.operations.calculate_extend_cost(line_name, new_points, extend_from_end, elevation_levels)
                except Exception as e:
                    QMessageBox.warning(dlg, "Error", str(e))
                    return
                
                reply = QMessageBox.question(dlg, "Confirm Line Extension",
                                            f"Extending this line will cost ${cost_info['cost']} (distance: {cost_info['length']:.2f} units). Proceed?",
                                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
                
                try:
                    result = self.operations.extend_line_complete(line_name, current_player, new_points, extend_from_end, elevation_levels)
                    self.check_and_eliminate_player(current_player)
                    QMessageBox.information(dlg, "Line Extension Started", 
                        f"Extension of line '{result['line_name']}' has been started.\n"
                        f"Points to extend: {result['points_count']}\n"
                        f"Cost: ${result['cost']}\n"
                        f"Estimated build time: {result['build_time']} turn(s)\n"
                        f"Remaining money: ${result['remaining_money']}\n"
                        f"The extension will appear in the Construction tab.")
                    dlg.close()
                    # OPTIMIZATION: Defer map refresh to prevent UI freeze
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(200, self.DisplayMapInfo)
                    self.refresh_construction_tab()
                except Exception as e:
                    QMessageBox.warning(dlg, "Error", f"Failed to extend line: {str(e)}")
            
            extend_btn.clicked.connect(do_extend)
            
            btn_layout = QHBoxLayout()
            save_btn = QPushButton("Save")
            cancel_btn = QPushButton("Close")
            btn_layout.addWidget(save_btn)
            btn_layout.addWidget(cancel_btn)
            layout.addLayout(btn_layout)
            
            def do_save():
                new_name = name_edit.text().strip()
                if not new_name:
                    QMessageBox.warning(dlg, "Error", "Line name cannot be empty.")
                    return
                try:
                    self.operations.rename_line(line, new_name)
                    QMessageBox.information(dlg, "Line Updated", "Line name has been updated.")
                    dlg.close()
                    self.DisplayMapInfo()
                except Exception as e:
                    QMessageBox.warning(dlg, "Error", f"Failed to update line: {str(e)}")
            
            save_btn.clicked.connect(do_save)
        else:
            btn_layout = QHBoxLayout()
            close_btn = QPushButton("Close")
            btn_layout.addWidget(close_btn)
            layout.addLayout(btn_layout)
            close_btn.clicked.connect(dlg.close)
        
        dlg.setLayout(layout)
        dlg.exec()
    
    def show_station_edit_dialog(self, station, line_name, line, x: int, y: int):
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Station")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel(f"Station: {station.name} at ({x}, {y})"))
        layout.addWidget(QLabel(f"Current line: {line_name}"))
        
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Station Name:"))
        name_edit = QLineEdit(station.name)
        name_layout.addWidget(name_edit)
        layout.addLayout(name_layout)
        
        layout.addWidget(QLabel("Lines serving this station:"))
        line_list = QWidget()
        line_list_layout = QVBoxLayout()
        line_list.setLayout(line_list_layout)
        
        all_lines = self.operations.get_map_lines()
        current_player = self.operations.get_current_player(self.current_player_index)
        player_line_names = self.operations.get_player_line_names(current_player) if current_player else []
        
        line_checkboxes = {}
        for line_name in station.get_line_names():
            checkbox = QCheckBox(line_name)
            checkbox.setChecked(True)
            line_list_layout.addWidget(checkbox)
            line_checkboxes[line_name] = checkbox
        
        for line_name, map_line in all_lines.items():
            if line_name not in line_checkboxes:
                if current_player and line_name in player_line_names:
                    checkbox = QCheckBox(line_name)
                    checkbox.setChecked(False)
                    line_list_layout.addWidget(checkbox)
                    line_checkboxes[line_name] = checkbox
        
        scroll = QScrollArea()
        scroll.setWidget(line_list)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(150)
        layout.addWidget(scroll)
        
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_save():
            new_name = name_edit.text().strip()
            if not new_name:
                QMessageBox.warning(dlg, "Error", "Station name cannot be empty.")
                return
            
            lines_to_add = []
            lines_to_remove = []
            
            original_line_names = set(station.get_line_names())
            selected_line_names = set()
            
            for line_name, checkbox in line_checkboxes.items():
                if checkbox.isChecked():
                    selected_line_names.add(line_name)
                    if line_name not in original_line_names:
                        lines_to_add.append(line_name)
            
            for line_name in original_line_names:
                if line_name not in selected_line_names:
                    lines_to_remove.append(line_name)
            
            try:
                if new_name != station.name:
                    self.operations.rename_station(station, new_name)
                
                if lines_to_add or lines_to_remove:
                    self.operations.modify_station_lines(station, lines_to_add, lines_to_remove)
                
                QMessageBox.information(dlg, "Station Updated", "Station has been updated successfully.")
                dlg.close()
                self.DisplayMapInfo()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", f"Failed to update station: {str(e)}")
        
        save_btn.clicked.connect(do_save)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_change_trainset_line(self, trainset, player):
        current_line = self.operations.get_trainset_line_assignment(trainset)
        line_names = self.operations.get_player_line_names(player)
        line_names.append("Unassign")
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Change Trainset Line Assignment")
        dlg.setGeometry(200, 200, 400, 200)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel(f"Current assignment: {current_line if current_line else 'Unassigned'}"))
        layout.addWidget(QLabel("Select new line:"))
        
        line_combo = QComboBox()
        line_combo.addItem("Unassign", None)
        for line_name in line_names:
            if line_name != "Unassign":
                line_combo.addItem(line_name, line_name)
        if current_line:
            idx = line_combo.findData(current_line)
            if idx >= 0:
                line_combo.setCurrentIndex(idx)
        
        layout.addWidget(line_combo)
        
        btn_layout = QHBoxLayout()
        assign_btn = QPushButton("Assign")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(assign_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)
        
        def do_assign():
            new_line = line_combo.currentData()
            try:
                if new_line:
                    self.operations.assign_trainset_to_line(trainset, new_line)
                    QMessageBox.information(dlg, "Line Assignment Changed", f"Trainset assigned to line '{new_line}'.")
                else:
                    prev_line = self.operations.get_trainset_line_assignment(trainset)
                    if prev_line:
                        all_lines = self.operations.get_map_lines()
                        prev_line_obj = all_lines.get(prev_line)
                        if prev_line_obj and trainset in prev_line_obj.vehicles:
                            prev_line_obj.vehicles.remove(trainset)
                        trainset.assign_to_line(None)
                    QMessageBox.information(dlg, "Line Assignment Changed", "Trainset unassigned from line.")
                dlg.close()
                self.DisplayRollingStockInfo(player)
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        
        assign_btn.clicked.connect(do_assign)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_unassign_trainset_clicked(self):
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        assigned = []
        for ts in self.operations.get_player_trainsets(current_player):
            if self.operations.get_trainset_line_assignment(ts) is not None:
                assigned.append(ts)
        
        if not assigned:
            QMessageBox.information(self, "No Assigned Trainsets", "You don't have any trainsets assigned to lines.")
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Unassign Trainset from Line")
        dlg.setGeometry(200, 200, 400, 200)
        l = QVBoxLayout()
        
        l.addWidget(QLabel("Select Trainset to Unassign:"))
        ts_combo = QComboBox()
        for i, ts in enumerate(assigned):
            loco_name = self.operations.get_trainset_locomotive_name(ts)
            line_name = self.operations.get_trainset_line_assignment(ts)
            opt = f"Trainset {i+1}: {loco_name} (on {line_name})"
            ts_combo.addItem(opt, ts)
        l.addWidget(ts_combo)
        
        btn_layout = QHBoxLayout()
        ok = QPushButton("Unassign")
        cancel = QPushButton("Cancel")
        btn_layout.addWidget(ok)
        btn_layout.addWidget(cancel)
        l.addLayout(btn_layout)
        dlg.setLayout(l)
        
        def do_unassign():
            selected_ts = ts_combo.currentData()
            try:
                
                result = self.operations.unassign_trainset_complete(selected_ts)
                QMessageBox.information(self, "Unassigned", f"Trainset unassigned from {result['line_name']}")
                dlg.close()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
        
        ok.clicked.connect(do_unassign)
        cancel.clicked.connect(dlg.close)
        dlg.exec()
    
    def on_disassemble_trainset_clicked(self):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        
        disassemble_info = self.operations.get_disassemble_info(current_player)
        if not disassemble_info['trainsets']:
            QMessageBox.information(self, "No Trainsets", "You don't have any trainsets to disassemble.")
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Disassemble Trainset")
        dlg.setGeometry(200, 200, 400, 200)
        l = QVBoxLayout()
        
        l.addWidget(QLabel("Select Trainset to Disassemble:"))
        ts_combo = QComboBox()
        for i, ts in enumerate(disassemble_info['trainsets']):
            loco_name = self.operations.get_trainset_locomotive_name(ts)
            num_cars = len(self.operations.get_trainset_cars(ts))
            line_name = self.operations.get_trainset_line_assignment(ts)
            line_info = f" (on {line_name})" if line_name else " (unassigned)"
            opt = f"Trainset {i+1}: {loco_name} ({num_cars} cars){line_info}"
            ts_combo.addItem(opt, ts)
        l.addWidget(ts_combo)
        
        btn_layout = QHBoxLayout()
        ok = QPushButton("Disassemble")
        cancel = QPushButton("Cancel")
        btn_layout.addWidget(ok)
        btn_layout.addWidget(cancel)
        l.addLayout(btn_layout)
        dlg.setLayout(l)
        
        def do_disassemble():
            selected_ts = ts_combo.currentData()
            
            
            line_name = self.operations.get_trainset_line_assignment(selected_ts)
            if line_name:
                reply = QMessageBox.question(
                    dlg, "Trainset Assigned", 
                    f"This trainset is assigned to line '{line_name}'. Disassembling will unassign it. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                
                self.operations.unassign_trainset_from_line(selected_ts, None)
            
            
            locomotive = self.operations.get_trainset_locomotive(selected_ts)
            railcars = self.operations.get_trainset_cars(selected_ts)
            
            
            self.operations.remove_player_trainset(current_player, selected_ts)
            
            
            if locomotive:
                self.operations.acquire_locomotive_for_player(current_player, locomotive)
            
            
            for railcar in railcars:
                self.operations.acquire_railcar_for_player(current_player, railcar)
            
            QMessageBox.information(
                dlg, "Trainset Disassembled", 
                f"Trainset disassembled!\nLocomotive: {locomotive.name if locomotive else 'None'}\n"
                f"Railcars: {len(railcars)}\nYou can now use these components to assemble new trainsets."
            )
            dlg.close()
        
        ok.clicked.connect(do_disassemble)
        cancel.clicked.connect(dlg.close)
        dlg.exec()

    def on_sell_trainset_clicked(self):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        
        trainsets = self.operations.get_player_trainsets(current_player)
        if not trainsets:
            QMessageBox.information(self, "No Trainsets", "You don't have any trainsets to sell.")
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Sell Trainset for Half Price")
        dlg.setGeometry(200, 200, 500, 300)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select trainset to sell (you will receive half its original purchase price):"))
        trainset_combo = QComboBox()
        trainset_info = []
        for i, trainset in enumerate(trainsets):
            loco_name = self.operations.get_trainset_locomotive_name(trainset)
            num_cars = len(self.operations.get_trainset_cars(trainset))
            capacity = self.operations.get_trainset_capacity(trainset)
            current_year = self.operations.get_year()
            original_price = trainset.get_price(current_year)
            sale_price = original_price // 2
            desc = f"Trainset {i+1}: {loco_name} ({num_cars} cars, cap={capacity}) - Sell for ${sale_price} (half of ${original_price})"
            trainset_combo.addItem(desc, trainset)
            trainset_info.append({'trainset': trainset, 'sale_price': sale_price, 'original_price': original_price})
        layout.addWidget(trainset_combo)
        
        
        def update_price_display(index):
            if 0 <= index < len(trainset_info):
                info = trainset_info[index]
                price_label.setText(f"Sale Price: ${info['sale_price']} (half of ${info['original_price']})")
        
        price_label = QLabel()
        if trainset_info:
            update_price_display(0)
        trainset_combo.currentIndexChanged.connect(update_price_display)
        layout.addWidget(price_label)
        
        btn_layout = QHBoxLayout()
        sell_btn = QPushButton("Sell for Half Price")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(sell_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_sell():
            selected_index = trainset_combo.currentIndex()
            if selected_index < 0 or selected_index >= len(trainsets):
                return
            
            selected_trainset = trainsets[selected_index]
            
            try:
                
                result = self.operations.sell_trainset_for_half_price(selected_trainset, current_player)
                QMessageBox.information(dlg, "Trainset Sold", 
                    f"Trainset sold for ${result['sale_price']} (half of ${result['original_price']}).\n"
                    f"New balance: ${result['new_money']}")
                dlg.close()
                
                self.DisplayMarketplaceInfo()
                self.DisplayRollingStockInfo(current_player)
                self.update_status_bar()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        
        sell_btn.clicked.connect(do_sell)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()
    
    def on_sell_trainset_from_rolling_stock(self, trainset, player):
        """Sell a trainset directly from the rolling stock display"""
        try:
            current_year = self.operations.get_year()
            original_price = trainset.get_price(current_year)
            sale_price = original_price // 2
            
            reply = QMessageBox.question(
                self,
                "Confirm Sale",
                f"Sell this trainset for ${sale_price} (half of ${original_price})?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                result = self.operations.sell_trainset_for_half_price(trainset, player)
                QMessageBox.information(
                    self,
                    "Trainset Sold",
                    f"Trainset sold for ${result['sale_price']} (half of ${result['original_price']}).\n"
                    f"New balance: ${result['new_money']}"
                )
                self.DisplayMarketplaceInfo()
                self.DisplayRollingStockInfo(player)
                self.update_status_bar()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
    
    def on_sell_locomotive_from_rolling_stock(self, locomotive, player):
        """Sell a locomotive directly from the rolling stock display"""
        try:
            current_year = self.operations.get_year()
            original_price = locomotive.get_price(current_year)
            sale_price = original_price // 2
            
            reply = QMessageBox.question(
                self,
                "Confirm Sale",
                f"Sell this locomotive for ${sale_price} (half of ${original_price})?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                result = self.operations.sell_locomotive_for_half_price(locomotive, player)
                QMessageBox.information(
                    self,
                    "Locomotive Sold",
                    f"Locomotive sold for ${result['sale_price']} (half of ${result['original_price']}).\n"
                    f"New balance: ${result['new_money']}"
                )
                self.DisplayMarketplaceInfo()
                self.DisplayRollingStockInfo(player)
                self.update_status_bar()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
    
    def on_sell_railcar_from_rolling_stock(self, railcar, player):
        """Sell a railcar directly from the rolling stock display"""
        try:
            current_year = self.operations.get_year()
            original_price = railcar.get_price(current_year)
            sale_price = original_price // 2
            
            reply = QMessageBox.question(
                self,
                "Confirm Sale",
                f"Sell this railcar for ${sale_price} (half of ${original_price})?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                result = self.operations.sell_railcar_for_half_price(railcar, player)
                QMessageBox.information(
                    self,
                    "Railcar Sold",
                    f"Railcar sold for ${result['sale_price']} (half of ${result['original_price']}).\n"
                    f"New balance: ${result['new_money']}"
                )
                self.DisplayMarketplaceInfo()
                self.DisplayRollingStockInfo(player)
                self.update_status_bar()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
    
    def on_add_railcar_clicked(self):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        add_info = self.operations.get_add_railcar_info(current_player)
        trainsets = add_info['trainsets']
        railcars = add_info['railcars']
        
        if not trainsets:
            QMessageBox.information(self, "No Trainsets", "You don't have any trainsets to add railcars to.")
            return
        
        if not railcars:
            QMessageBox.information(self, "No Railcars", "You don't have any railcars. Buy some from the marketplace first.")
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Railcar to Trainset")
        dlg.setGeometry(200, 200, 500, 300)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select Trainset:"))
        trainset_combo = QComboBox()
        for i, ts in enumerate(trainsets):
            loco_name = self.operations.get_trainset_locomotive_name(ts)
            num_cars = len(self.operations.get_trainset_cars(ts))
            # Get max cars from locomotive, or use default
            locomotive = self.operations.get_trainset_locomotive(ts)
            max_cars = locomotive.max_cars if locomotive and locomotive.max_cars is not None else 5
            loco = getattr(ts, 'locomotive', None)
            can_have_cars = getattr(loco, 'can_have_railcars', True) if loco else True
            if not can_have_cars:
                trainset_combo.addItem(f"Trainset {i+1}: {loco_name} (self-contained, no railcars)", ts)
            else:
                trainset_combo.addItem(f"Trainset {i+1}: {loco_name} ({num_cars}/{max_cars} cars)", ts)
        layout.addWidget(trainset_combo)
        
        layout.addWidget(QLabel("Select Railcar:"))
        railcar_combo = QComboBox()
        for i, car in enumerate(add_info['railcars']):
            car_name = self.operations.get_vehicle_name(car)
            capacity = self.operations.get_vehicle_capacity(car)
            railcar_combo.addItem(f"{car_name} (cap: {capacity})", car)
        layout.addWidget(railcar_combo)
        
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_add():
            selected_trainset = trainset_combo.currentData()
            selected_railcar = railcar_combo.currentData()
            
            if not selected_trainset or not selected_railcar:
                QMessageBox.warning(dlg, "Error", "Please select both a trainset and a railcar.")
                return
            
            
            # Get max cars from locomotive, or use default
            locomotive = self.operations.get_trainset_locomotive(selected_trainset)
            max_cars = locomotive.max_cars if locomotive and locomotive.max_cars is not None else 5
            if len(self.operations.get_trainset_cars(selected_trainset)) >= max_cars:
                QMessageBox.warning(dlg, "Error", f"This trainset already has the maximum of {max_cars} railcars.")
                return
            
            
            if self.operations.add_railcar_to_trainset(selected_trainset, selected_railcar):
                
                railcars.remove(selected_railcar)
                QMessageBox.information(dlg, "Railcar Added", 
                    f"Railcar added to trainset!\nNew capacity: {self.operations.get_trainset_capacity(selected_trainset)}\nRailcars: {len(self.operations.get_trainset_cars(selected_trainset))}/{max_cars}")
                dlg.close()
            else:
                QMessageBox.warning(dlg, "Error", "Failed to add railcar. Trainset may be at maximum capacity.")
        
        add_btn.clicked.connect(do_add)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()
    
    def on_sell_to_historical_society_clicked(self):
        
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        current_year = self.operations.get_year()
        eligible_items = self.operations.get_historical_society_items(current_player, current_year)
        
        if not eligible_items['trainsets'] and not eligible_items['locomotives']:
            QMessageBox.information(self, "No Eligible Items", 
                "You don't have any trainsets or locomotives that are over 100 years old to sell to the Historical Society.")
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Sell to Historical Society")
        dlg.setGeometry(200, 200, 600, 400)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select item to sell (5x original price for items over 100 years old):"))
        item_combo = QComboBox()
        
        for item_info in eligible_items['trainsets']:
            item_combo.addItem(
                f"Trainset: {item_info['name']} ({item_info['years_old']} years old) - ${item_info['price']}", 
                (item_info['type'], item_info['item'])
            )
        
        for item_info in eligible_items['locomotives']:
            item_combo.addItem(
                f"Locomotive: {item_info['name']} ({item_info['years_old']} years old) - ${item_info['price']}", 
                (item_info['type'], item_info['item'])
            )
        
        layout.addWidget(item_combo)
        
        btn_layout = QHBoxLayout()
        sell_btn = QPushButton("Sell")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(sell_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_sell():
            item_data = item_combo.currentData()
            if not item_data:
                QMessageBox.warning(dlg, "Error", "Please select an item to sell.")
                return
            
            item_type, item = item_data
            
            try:
                
                result = self.operations.sell_to_historical_society_complete(item, item_type, current_player, current_year)
                item_name = "Trainset" if item_type == 'trainset' else "Locomotive"
                QMessageBox.information(dlg, "Sold to Historical Society", 
                    f"{item_name} sold for ${result['price']}!\nThe Historical Society appreciates preserving this piece of history.")
                dlg.close()
                
                self.DisplayMarketplaceInfo()
                self.DisplayRollingStockInfo(current_player)
                self.update_status_bar()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        
        sell_btn.clicked.connect(do_sell)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def DisplayMarketplaceInfo(self):
        try:
            if self.operations is None:
                return
            if not hasattr(self, 'player_tabs_dict') or not self.player_tabs_dict:
                return
            
            lines = self.operations.marketplace_get_available_lines()
            vehicles = self.operations.marketplace_get_available_vehicles()
            current_year = self.operations.get_year()
            
            if lines is None:
                lines = []
            if vehicles is None:
                vehicles = []

            line_groups = {}
            if lines:
                for line_sale_item in lines:
                    item = line_sale_item.get_item()
                    line_name = item.name
                    seller = line_sale_item.get_seller()
                    key = (line_name, seller)
                    if key not in line_groups:
                        line_groups[key] = []
                    line_groups[key].append(line_sale_item)
            
        
            from vehicle import Trainset, Locomotive, Railcar
            trainset_groups = {}
            loco_groups = {}
            ferry_groups = {}
            horses_trolleys_groups = {}
            railcar_groups = {}
            other_vehicles = []
            if vehicles:
                for sale_item in vehicles:
                    item = sale_item.get_item()
                    if isinstance(item, Trainset):
                        loco_name = self.operations.get_trainset_locomotive_name(item)
                        num_cars = len(self.operations.get_trainset_cars(item))
                        capacity = self.operations.get_trainset_capacity(item)
                        year = self.operations.get_trainset_locomotive_year(item)
                        seller = sale_item.get_seller()
                        if year and year > current_year:
                            continue
                        key = (loco_name, num_cars, capacity, seller)
                        if key not in trainset_groups:
                            trainset_groups[key] = []
                        trainset_groups[key].append(sale_item)
                    elif isinstance(item, Locomotive):
                        year = getattr(item, 'year_introduced', None)
                        item_name = getattr(item, 'name', 'Unknown')
                        seller = sale_item.get_seller()
                        if year and year > current_year:
                            continue
                        # Locomotives go in locomotives category
                        is_ferry = False
                        # Check if it's a horse-drawn vehicle (name contains "horse" or fuel is Food)
                        is_horse = 'horse' in item_name.lower()
                        if not is_horse:
                            from game_resource import Food
                            is_horse = isinstance(getattr(item, 'fuel', None), Food)
                        # Check if it's a trolley (overhead wire, self-contained, no third rail)
                        needs_overhead_wire = getattr(item, 'needs_overhead_wire', False)
                        can_have_cars = getattr(item, 'can_have_railcars', True)
                        needs_third_rail = getattr(item, 'needs_third_rail', False)
                        is_trolley = needs_overhead_wire and not can_have_cars and not needs_third_rail
                        # Also check if name contains trolley/streetcar/LRV
                        if not is_trolley:
                            item_name_lower = item_name.lower()
                            is_trolley = any(term in item_name_lower for term in ['trolley', 'streetcar', 'lrv', 'pcc', 'birney'])
                        
                        key = (item_name, seller)
                        if is_ferry:
                            if key not in ferry_groups:
                                ferry_groups[key] = []
                            ferry_groups[key].append(sale_item)
                        elif is_horse or is_trolley:
                            if key not in horses_trolleys_groups:
                                horses_trolleys_groups[key] = []
                            horses_trolleys_groups[key].append(sale_item)
                        else:
                            if key not in loco_groups:
                                loco_groups[key] = []
                            loco_groups[key].append(sale_item)
                    elif isinstance(item, Ferry):  # Ferry already imported at top of function
                        year = getattr(item, 'year_introduced', None)
                        item_name = getattr(item, 'name', 'Unknown')
                        seller = sale_item.get_seller()
                        if year and year > current_year:
                            continue
                        key = (item_name, seller)
                        if key not in ferry_groups:
                            ferry_groups[key] = []
                        ferry_groups[key].append(sale_item)
                    elif isinstance(item, Bus):
                        year = getattr(item, 'year_introduced', None)
                        item_name = getattr(item, 'name', 'Unknown')
                        seller = sale_item.get_seller()
                        if year and year > current_year:
                            continue
                        key = (item_name, seller)
                        # Buses go in horses_trolleys_groups for now, or create a separate bus_groups if needed
                        if key not in horses_trolleys_groups:
                            horses_trolleys_groups[key] = []
                        horses_trolleys_groups[key].append(sale_item)
                    elif isinstance(item, Railcar):
                        year = getattr(item, 'year_introduced', None) if hasattr(item, 'year_introduced') else None
                        item_name = getattr(item, 'name', 'Unknown')
                        capacity = getattr(item, 'capacity', 0)
                        seller = sale_item.get_seller()
                        if year and year > current_year:
                            continue
                        key = (item_name, capacity, seller)
                        if key not in railcar_groups:
                            railcar_groups[key] = []
                        railcar_groups[key].append(sale_item)
                    else:
                        other_vehicles.append(sale_item)
            
        
            for player_id, player_tabs_dict in self.player_tabs_dict.items():
                marketplace_lines_layout = player_tabs_dict.get('marketplace_lines_layout')
                marketplace_trainsets_layout = player_tabs_dict.get('marketplace_trainsets_layout')
                marketplace_locomotives_layout = player_tabs_dict.get('marketplace_locomotives_layout')
                marketplace_ferries_layout = player_tabs_dict.get('marketplace_ferries_layout')
                marketplace_horses_trolleys_layout = player_tabs_dict.get('marketplace_horses_trolleys_layout')
                marketplace_railcars_layout = player_tabs_dict.get('marketplace_railcars_layout')
                marketplace_resources_layout = player_tabs_dict.get('marketplace_resources_layout')
                marketplace_workers_layout = player_tabs_dict.get('marketplace_workers_layout')
            
                if (
                    marketplace_lines_layout is None
                    or marketplace_trainsets_layout is None
                    or marketplace_locomotives_layout is None
                    or marketplace_ferries_layout is None
                    or marketplace_horses_trolleys_layout is None
                    or marketplace_railcars_layout is None
                    or marketplace_resources_layout is None
                    or marketplace_workers_layout is None
                ):
                    continue
            
            
                for layout in [marketplace_lines_layout, marketplace_trainsets_layout,
                          marketplace_locomotives_layout, marketplace_ferries_layout,
                          marketplace_horses_trolleys_layout, marketplace_railcars_layout,
                          marketplace_resources_layout, marketplace_workers_layout]:
                    while layout.count():
                        child = layout.takeAt(0)
                        if child.widget():
                            child.widget().deleteLater()
            
                players = self.operations.get_players()
                player = next((p for p in players if self.operations.get_player_id(p) == player_id), None)
            
                lines_label = QLabel("Available Lines:")
                marketplace_lines_layout.addWidget(lines_label)
                if line_groups:
                    for (line_name, seller), sale_items in line_groups.items():
                        total_qty = sum(si.get_quantity_available() for si in sale_items)
                        price = sale_items[0].get_price(current_year)  # This already includes sale discount
                        sale_item = sale_items[0]
                        has_discount = hasattr(sale_item, 'discount_percent') and sale_item.discount_percent > 0 and hasattr(sale_item, 'sale_year') and sale_item.sale_year == current_year
                        sale_text = ""
                        if has_discount:
                            # Calculate what the original price would be with inflation (for display)
                            original_base_price = getattr(sale_item, 'original_price', sale_item.price)
                            # Apply inflation to original price for display
                            original_price = int(original_base_price * get_inflation_factor(current_year))
                            sale_text = f" <span style='color: red; font-weight: bold;'>SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
                        line_info = QLabel(f"• {line_name}: ${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}")
                        line_info.setTextFormat(Qt.TextFormat.RichText)
                        marketplace_lines_layout.addWidget(line_info)
                else:
                    no_lines_label = QLabel("No lines available")
                    marketplace_lines_layout.addWidget(no_lines_label)
            
                marketplace_lines_layout.addStretch()
            
                trainset_label = QLabel("Available Trainsets:")
                marketplace_trainsets_layout.addWidget(trainset_label)
            
                if trainset_groups:
                        for (loco_name, num_cars, capacity, seller), sale_items in trainset_groups.items():
                            total_qty = sum(si.get_quantity_available() for si in sale_items)
                            sale_item = sale_items[0]
                            price = sale_item.get_price(current_year)  # This already includes sale discount
                            has_discount = hasattr(sale_item, 'discount_percent') and sale_item.discount_percent > 0 and hasattr(sale_item, 'sale_year') and sale_item.sale_year == current_year
                            sale_text = ""
                            if has_discount:
                                # Calculate what the original price would be with inflation (for display)
                                original_base_price = getattr(sale_item, 'original_price', sale_item.price)
                                # Apply inflation to original price for display
                                inflation_factor = 1.0 + (current_year - 1830) * (10.0 - 1.0) / (2050 - 1830)
                                inflation_factor = max(1.0, min(10.0, inflation_factor))
                                original_price = int(original_base_price * inflation_factor)
                                sale_text = f" <span style='color: red; font-weight: bold;'>SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
                            item_layout = QHBoxLayout()
                            trainset_item = sale_item.get_item()
                            quality_info = ""
                            if hasattr(trainset_item, 'locomotive') and trainset_item.locomotive:
                                loco = trainset_item.locomotive
                                speed = getattr(loco, 'speed', 'N/A')
                                quality_info = f" | Speed: {speed} mph"
                            item_info = QLabel(f"• Trainset ({loco_name}, {num_cars} cars, cap={capacity}{quality_info}): ${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}")
                            item_info.setTextFormat(Qt.TextFormat.RichText)
                            item_layout.addWidget(item_info)
                            if total_qty > 0:
                                buy_button = QPushButton("Buy")
                                first_available = next((si for si in sale_items if si.get_quantity_available() > 0), sale_items[0])
                                def make_buy_handler(sale_item):
                                    return lambda checked: self.buy_trainset_from_marketplace(sale_item)
                                buy_button.clicked.connect(make_buy_handler(first_available))
                                item_layout.addWidget(buy_button)
                            item_layout.addStretch()
                            item_widget = QWidget()
                            item_widget.setLayout(item_layout)
                            marketplace_trainsets_layout.addWidget(item_widget)
                else:
                    no_trainsets_label = QLabel("No trainsets available")
                    marketplace_trainsets_layout.addWidget(no_trainsets_label)
            
                marketplace_trainsets_layout.addStretch()
            
                loco_label = QLabel("Available Locomotives:")
                marketplace_locomotives_layout.addWidget(loco_label)
            
                if loco_groups:
                    for (item_name, seller), sale_items in loco_groups.items():
                        total_qty = sum(si.get_quantity_available() for si in sale_items)
                        sale_item = sale_items[0]
                        price = sale_item.get_price(current_year)  # This already includes sale discount
                        has_discount = hasattr(sale_item, 'discount_percent') and sale_item.discount_percent > 0 and hasattr(sale_item, 'sale_year') and sale_item.sale_year == current_year
                        sale_text = ""
                        if has_discount:
                            # Calculate what the original price would be with inflation (for display)
                            original_base_price = getattr(sale_item, 'original_price', sale_item.price)
                            # Apply inflation to original price for display
                            original_price = int(original_base_price * get_inflation_factor(current_year))
                            sale_text = f" <span style='color: red; font-weight: bold;'>SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
                        item_layout = QHBoxLayout()
                        item_info = QLabel(f"• Locomotive ({item_name}): ${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}")
                        item_info.setTextFormat(Qt.TextFormat.RichText)
                        item_layout.addWidget(item_info)
                        if total_qty > 0:
                            buy_button = QPushButton("Buy")
                            first_available = next((si for si in sale_items if si.get_quantity_available() > 0), sale_items[0])
                            buy_button.clicked.connect(lambda checked, si=first_available: self.buy_vehicle_from_marketplace(si))
                            item_layout.addWidget(buy_button)
                        item_layout.addStretch()
                        item_widget = QWidget()
                        item_widget.setLayout(item_layout)
                        marketplace_locomotives_layout.addWidget(item_widget)
                
                marketplace_locomotives_layout.addStretch()
                if not loco_groups:
                    no_loco_label = QLabel("No locomotives available")
                    marketplace_locomotives_layout.addWidget(no_loco_label)
            
                ferry_label = QLabel("Available Ferries:")
                marketplace_ferries_layout.addWidget(ferry_label)
            
                if ferry_groups:
                    for (item_name, seller), sale_items in ferry_groups.items():
                        total_qty = sum(si.get_quantity_available() for si in sale_items)
                        sale_item = sale_items[0]
                        price = sale_item.get_price(current_year)  # This already includes sale discount
                        has_discount = hasattr(sale_item, 'discount_percent') and sale_item.discount_percent > 0 and hasattr(sale_item, 'sale_year') and sale_item.sale_year == current_year
                        sale_text = ""
                        if has_discount:
                            # Calculate what the original price would be with inflation (for display)
                            original_base_price = getattr(sale_item, 'original_price', sale_item.price)
                            # Apply inflation to original price for display
                            original_price = int(original_base_price * get_inflation_factor(current_year))
                            sale_text = f" <span style='color: red; font-weight: bold;'>SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
                        item_layout = QHBoxLayout()
                        item_info = QLabel(f"• Ferry ({item_name}): ${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}")
                        item_info.setTextFormat(Qt.TextFormat.RichText)
                        item_layout.addWidget(item_info)
                        if total_qty > 0:
                            buy_button = QPushButton("Buy")
                            first_available = next((si for si in sale_items if si.get_quantity_available() > 0), sale_items[0])
                            buy_button.clicked.connect(lambda checked, si=first_available: self.buy_vehicle_from_marketplace(si))
                            item_layout.addWidget(buy_button)
                        item_layout.addStretch()
                        item_widget = QWidget()
                        item_widget.setLayout(item_layout)
                        marketplace_ferries_layout.addWidget(item_widget)
                
                marketplace_ferries_layout.addStretch()
                if not ferry_groups:
                    no_ferry_label = QLabel("No ferries available")
                    marketplace_ferries_layout.addWidget(no_ferry_label)
            
                horses_trolleys_label = QLabel("Available Horses & Trolleys:")
                marketplace_horses_trolleys_layout.addWidget(horses_trolleys_label)
            
                if horses_trolleys_groups:
                    for (item_name, seller), sale_items in horses_trolleys_groups.items():
                        total_qty = sum(si.get_quantity_available() for si in sale_items)
                        sale_item = sale_items[0]
                        price = sale_item.get_price(current_year)  # This already includes sale discount
                        has_discount = hasattr(sale_item, 'discount_percent') and sale_item.discount_percent > 0 and hasattr(sale_item, 'sale_year') and sale_item.sale_year == current_year
                        sale_text = ""
                        if has_discount:
                            # Calculate what the original price would be with inflation (for display)
                            original_base_price = getattr(sale_item, 'original_price', sale_item.price)
                            # Apply inflation to original price for display
                            original_price = int(original_base_price * get_inflation_factor(current_year))
                            sale_text = f" <span style='color: red; font-weight: bold;'>SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
                        item_layout = QHBoxLayout()
                        item_info = QLabel(f"• {item_name}: ${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}")
                        item_info.setTextFormat(Qt.TextFormat.RichText)
                        item_layout.addWidget(item_info)
                        if total_qty > 0:
                            buy_button = QPushButton("Buy")
                            first_available = next((si for si in sale_items if si.get_quantity_available() > 0), sale_items[0])
                            buy_button.clicked.connect(lambda checked, si=first_available: self.buy_vehicle_from_marketplace(si))
                            item_layout.addWidget(buy_button)
                        item_layout.addStretch()
                        item_widget = QWidget()
                        item_widget.setLayout(item_layout)
                        marketplace_horses_trolleys_layout.addWidget(item_widget)
                
                marketplace_horses_trolleys_layout.addStretch()
                if not horses_trolleys_groups:
                    no_horses_trolleys_label = QLabel("No horses or trolleys available")
                    marketplace_horses_trolleys_layout.addWidget(no_horses_trolleys_label)
            
                railcar_label = QLabel("Available Railcars:")
                marketplace_railcars_layout.addWidget(railcar_label)
            
                if railcar_groups:
                        for (item_name, capacity, seller), sale_items in railcar_groups.items():
                            total_qty = sum(si.get_quantity_available() for si in sale_items)
                            sale_item = sale_items[0]
                            price = sale_item.get_price(current_year)
                            has_discount = hasattr(sale_item, 'discount_percent') and sale_item.discount_percent > 0
                            sale_text = ""
                            if has_discount:
                                original_price = getattr(sale_item, 'original_price', price)
                                sale_text = f" <span style='color: red; font-weight: bold;'>SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
                            item_layout = QHBoxLayout()
                            item_info = QLabel(f"• Railcar ({item_name}, cap={capacity}): ${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}")
                            item_info.setTextFormat(Qt.TextFormat.RichText)
                            item_layout.addWidget(item_info)
                            if total_qty > 0:
                                buy_button = QPushButton("Buy")
                                first_available = next((si for si in sale_items if si.get_quantity_available() > 0), sale_items[0])
                                buy_button.clicked.connect(lambda checked, si=first_available: self.buy_vehicle_from_marketplace(si))
                                item_layout.addWidget(buy_button)
                            item_layout.addStretch()
                            item_widget = QWidget()
                            item_widget.setLayout(item_layout)
                            marketplace_railcars_layout.addWidget(item_widget)
                
                marketplace_railcars_layout.addStretch()
                if not railcar_groups:
                    no_railcar_label = QLabel("No railcars available")
                    marketplace_railcars_layout.addWidget(no_railcar_label)
            
                resources_label = QLabel("Current Resource Costs (per trainset per year):")
                marketplace_resources_layout.addWidget(resources_label)
            
                resource_costs = self.operations.get_resource_costs(current_year)
                for resource_name, cost in sorted(resource_costs.items()):
                    cost_text = f"${cost}" if cost > 0 else "Free"
                    resource_info = QLabel(f"• {resource_name}: {cost_text}")
                    marketplace_resources_layout.addWidget(resource_info)
            
                info_label = QLabel("\nNote: Resource costs change historically and are updated each year.")
                info_label.setWordWrap(True)
                marketplace_resources_layout.addWidget(info_label)
                marketplace_resources_layout.addStretch()
            
                from worker import ENGINEER, LAWYER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER
                workers_label = QLabel("Available Workers:")
                marketplace_workers_layout.addWidget(workers_label)
            
                available_supply = self.operations.marketplace_get_worker_supply(ENGINEER.name)
                engineer_actual_cost = self.operations.calculate_worker_rental_cost(ENGINEER, 1, current_year)
                engineer_info_label = QLabel(f"Engineer: Required to operate trainsets\nYearly cost per engineer: ${engineer_actual_cost}\nAvailable supply: {available_supply}")
                marketplace_workers_layout.addWidget(engineer_info_label)
            
                if player:
                    engineer_count = self.operations.get_player_engineer_count(player)
                    assigned_count = self.operations.get_player_assigned_trainsets_count(player)
                    available_engineers = engineer_count - assigned_count
                    current_info = QLabel(
                        f"Your Engineers: {engineer_count} (Assigned: {assigned_count}, Available: {available_engineers})"
                    )
                    if available_engineers < 0:
                        current_info.setStyleSheet("color: red; font-weight: bold;")
                else:
                    current_info = QLabel("Your Engineers: N/A")

                marketplace_workers_layout.addWidget(current_info)

                rent_layout = QHBoxLayout()
                rent_layout.addWidget(QLabel("Rent Engineers:"))
                engineer_spin = QSpinBox()
                engineer_spin.setMinimum(1)
                max_engineers = min(100, available_supply) if available_supply > 0 else 1
                engineer_spin.setMaximum(max_engineers)
                engineer_spin.setValue(1)
                rent_layout.addWidget(engineer_spin)

                def update_cost():
                    qty = engineer_spin.value()
                    current_supply = self.operations.marketplace_get_worker_supply(ENGINEER.name)
                    new_max = min(100, current_supply) if current_supply > 0 else 1
                    if engineer_spin.maximum() != new_max:
                        engineer_spin.setMaximum(new_max)
                        if qty > new_max:
                            engineer_spin.setValue(new_max)
                            qty = new_max
                    actual_total_cost = self.operations.calculate_worker_rental_cost(ENGINEER, qty, current_year)
                    total_cost_label.setText(f"Total yearly cost: ${actual_total_cost}")
                
                initial_total_cost = self.operations.calculate_worker_rental_cost(ENGINEER, 1, current_year)
                total_cost_label = QLabel(f"Total yearly cost: ${initial_total_cost}")
                rent_layout.addWidget(total_cost_label)
                
                update_cost()

                engineer_spin.valueChanged.connect(update_cost)

                rent_btn = QPushButton("Rent")
                rent_btn.clicked.connect(lambda checked=False, worker=ENGINEER, qty_spin=engineer_spin, year=current_year: self.on_rent_workers_clicked(worker, qty_spin.value(), year))
                rent_layout.addWidget(rent_btn)

                rent_widget = QWidget()
                rent_widget.setLayout(rent_layout)
                marketplace_workers_layout.addWidget(rent_widget)

                available_lawyer_supply = self.operations.marketplace_get_worker_supply(LAWYER.name)
                lawyer_actual_cost = self.operations.calculate_worker_rental_cost(LAWYER, 1, current_year)
                lawyer_info_label = QLabel(
                    f"Lawyer: Helps win lawsuits\nYearly cost per lawyer: ${lawyer_actual_cost}\nAvailable supply: {available_lawyer_supply}"
                )
                marketplace_workers_layout.addWidget(lawyer_info_label)

                if player:
                    lawyer_count = self.operations.get_player_lawyer_count(player)
                    lawyer_current_info = QLabel(f"Your lawyers: {lawyer_count}")
                    marketplace_workers_layout.addWidget(lawyer_current_info)

                    lawyer_rent_layout = QHBoxLayout()
                    lawyer_rent_layout.addWidget(QLabel("Rent Lawyers:"))
                    lawyer_spin = QSpinBox()
                    lawyer_spin.setMinimum(1)
                    max_lawyers = min(100, available_lawyer_supply) if available_lawyer_supply > 0 else 1
                    lawyer_spin.setMaximum(max_lawyers)
                    lawyer_spin.setValue(1)
                    lawyer_rent_layout.addWidget(lawyer_spin)

                    def update_lawyer_cost():
                        qty = lawyer_spin.value()
                        current_supply = self.operations.marketplace_get_worker_supply(LAWYER.name)
                        new_max = min(100, current_supply) if current_supply > 0 else 1
                        if lawyer_spin.maximum() != new_max:
                            lawyer_spin.setMaximum(new_max)
                            if qty > new_max:
                                lawyer_spin.setValue(new_max)
                                qty = new_max
                        total_cost = self.operations.calculate_worker_rental_cost(LAWYER, qty, current_year)
                        lawyer_total_cost_label.setText(f"Total yearly cost: ${total_cost}")
                    
                    initial_lawyer_cost = self.operations.calculate_worker_rental_cost(LAWYER, 1, current_year)
                    lawyer_total_cost_label = QLabel(f"Total yearly cost: ${initial_lawyer_cost}")
                    lawyer_rent_layout.addWidget(lawyer_total_cost_label)
                    update_lawyer_cost()
                    lawyer_spin.valueChanged.connect(update_lawyer_cost)

                    lawyer_rent_btn = QPushButton("Rent")
                    lawyer_rent_btn.clicked.connect(lambda checked=False, worker=LAWYER, qty_spin=lawyer_spin, year=current_year: self.on_rent_workers_clicked(worker, qty_spin.value(), year))
                    lawyer_rent_layout.addWidget(lawyer_rent_btn)

                    lawyer_rent_widget = QWidget()
                    lawyer_rent_widget.setLayout(lawyer_rent_layout)
                    marketplace_workers_layout.addWidget(lawyer_rent_widget)

                available_construction_supply = self.operations.marketplace_get_worker_supply(CONSTRUCTION_WORKER.name)
                construction_actual_cost = self.operations.calculate_worker_rental_cost(CONSTRUCTION_WORKER, 1, current_year)
                construction_info_label = QLabel(
                    f"Construction Worker: Required to build lines and stations\nYearly cost per construction worker: ${construction_actual_cost}\nAvailable supply: {available_construction_supply}"
                )
                marketplace_workers_layout.addWidget(construction_info_label)

                if player:
                    construction_count = self.operations.get_player_construction_worker_count(player)
                    construction_current_info = QLabel(f"Your construction workers: {construction_count}")
                    marketplace_workers_layout.addWidget(construction_current_info)

                    construction_rent_layout = QHBoxLayout()
                    construction_rent_layout.addWidget(QLabel("Rent Construction Workers:"))
                    construction_spin = QSpinBox()
                    construction_spin.setMinimum(1)
                    max_construction = min(100, available_construction_supply) if available_construction_supply > 0 else 1
                    construction_spin.setMaximum(max_construction)
                    construction_spin.setValue(1)
                    construction_rent_layout.addWidget(construction_spin)

                    def update_construction_cost():
                        qty = construction_spin.value()
                        current_supply = self.operations.marketplace_get_worker_supply(CONSTRUCTION_WORKER.name)
                        new_max = min(100, current_supply) if current_supply > 0 else 1
                        if construction_spin.maximum() != new_max:
                            construction_spin.setMaximum(new_max)
                            if qty > new_max:
                                construction_spin.setValue(new_max)
                                qty = new_max
                        total_cost = self.operations.calculate_worker_rental_cost(CONSTRUCTION_WORKER, qty, current_year)
                        construction_total_cost_label.setText(f"Total yearly cost: ${total_cost}")
                    
                    initial_construction_cost = self.operations.calculate_worker_rental_cost(CONSTRUCTION_WORKER, 1, current_year)
                    construction_total_cost_label = QLabel(f"Total yearly cost: ${initial_construction_cost}")
                    construction_rent_layout.addWidget(construction_total_cost_label)
                    update_construction_cost()
                    construction_spin.valueChanged.connect(update_construction_cost)

                    construction_rent_btn = QPushButton("Rent")
                    construction_rent_btn.clicked.connect(
                        lambda checked=False, worker=CONSTRUCTION_WORKER, qty_spin=construction_spin, year=current_year: self.on_rent_workers_clicked(worker, qty_spin.value(), year)
                    )
                    construction_rent_layout.addWidget(construction_rent_btn)

                    construction_rent_widget = QWidget()
                    construction_rent_widget.setLayout(construction_rent_layout)
                    marketplace_workers_layout.addWidget(construction_rent_widget)

                available_maintenance_supply = self.operations.marketplace_get_worker_supply(MAINTENANCE_WORKER.name)
                maintenance_actual_cost = self.operations.calculate_worker_rental_cost(MAINTENANCE_WORKER, 1, current_year)
                maintenance_info_label = QLabel(
                    f"Maintenance Worker: Improves line and station quality\nYearly cost per maintenance worker: ${maintenance_actual_cost}\nAvailable supply: {available_maintenance_supply}"
                )
                marketplace_workers_layout.addWidget(maintenance_info_label)

                if player:
                    maintenance_rental = self.operations.get_player_worker_rental(player, MAINTENANCE_WORKER.name)
                    maintenance_count = maintenance_rental.quantity if maintenance_rental else 0
                    maintenance_current_info = QLabel(f"Your maintenance workers: {maintenance_count}")
                    marketplace_workers_layout.addWidget(maintenance_current_info)

                    maintenance_rent_layout = QHBoxLayout()
                    maintenance_rent_layout.addWidget(QLabel("Rent Maintenance Workers:"))
                    maintenance_spin = QSpinBox()
                    maintenance_spin.setMinimum(1)
                    max_maintenance = min(100, available_maintenance_supply) if available_maintenance_supply > 0 else 1
                    maintenance_spin.setMaximum(max_maintenance)
                    maintenance_spin.setValue(1)
                    maintenance_rent_layout.addWidget(maintenance_spin)

                    def update_maintenance_cost():
                        qty = maintenance_spin.value()
                        current_supply = self.operations.marketplace_get_worker_supply(MAINTENANCE_WORKER.name)
                        new_max = min(100, current_supply) if current_supply > 0 else 1
                        if maintenance_spin.maximum() != new_max:
                            maintenance_spin.setMaximum(new_max)
                            if qty > new_max:
                                maintenance_spin.setValue(new_max)
                                qty = new_max
                        total_cost = self.operations.calculate_worker_rental_cost(MAINTENANCE_WORKER, qty, current_year)
                        maintenance_total_cost_label.setText(f"Total yearly cost: ${total_cost}")
                    
                    initial_maintenance_cost = self.operations.calculate_worker_rental_cost(MAINTENANCE_WORKER, 1, current_year)
                    maintenance_total_cost_label = QLabel(f"Total yearly cost: ${initial_maintenance_cost}")
                    maintenance_rent_layout.addWidget(maintenance_total_cost_label)
                    update_maintenance_cost()
                    maintenance_spin.valueChanged.connect(update_maintenance_cost)

                    maintenance_rent_btn = QPushButton("Rent")
                    maintenance_rent_btn.clicked.connect(
                        lambda checked=False, worker=MAINTENANCE_WORKER, qty_spin=maintenance_spin, year=current_year: self.on_rent_workers_clicked(worker, qty_spin.value(), year)
                    )
                    maintenance_rent_layout.addWidget(maintenance_rent_btn)

                    maintenance_rent_widget = QWidget()
                    maintenance_rent_widget.setLayout(maintenance_rent_layout)
                    marketplace_workers_layout.addWidget(maintenance_rent_widget)

                marketplace_workers_layout.addStretch()
        except Exception as e:
            import traceback
            print(f"Error in DisplayMarketplaceInfo: {e}")
            traceback.print_exc()

    def ViewMarketplaceButton(self):
        
        pass

    def view_marketplace_button_clicked(self):
        self.DisplayMarketplaceInfo()
    
    def on_rent_workers_clicked(self, worker_type, quantity: int, current_year: int):
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Player", "No active player selected.")
            return
        
        available_supply = self.operations.marketplace_get_worker_supply(worker_type.name)
        
        if quantity > available_supply:
            QMessageBox.warning(self, "Insufficient Supply", 
                              f"Only {available_supply} {worker_type.name}(s) available. Requested: {quantity}.")
            return
        
        total_cost = self.operations.calculate_worker_rental_cost(worker_type, quantity, current_year)
        
        if self.operations.get_player_money(current_player) < total_cost:
            QMessageBox.warning(self, "Insufficient Funds", 
                              f"Not enough money. Need ${total_cost} for {quantity} {worker_type.name}(s).")
            return
        
        self.operations.rent_workers_for_player(current_player, worker_type, quantity, current_year)
        self.operations.marketplace_deduct_worker_supply(worker_type.name, quantity)
        self.operations.deduct_player_money(current_player, total_cost)
        
        # Auto-assign construction workers if they were rented
        from worker import CONSTRUCTION_WORKER
        if worker_type == CONSTRUCTION_WORKER:
            self.operations.auto_assign_unassigned_workers(current_player)
            self.refresh_construction_tab()
        
        QMessageBox.information(self, "Workers Rented", 
                               f"Successfully rented {quantity} {worker_type.name}(s) for ${total_cost} per year.")
        
        self.DisplayMarketplaceInfo()
        self.DisplayWorkersControls(current_player)
        self.update_status_bar()
        self.DisplayMarketplaceInfo()
    
    def on_fire_workers_clicked(self, player, worker_type):
        rental = self.operations.get_player_worker_rental(player, worker_type.name)
        if not rental or rental.quantity == 0:
            QMessageBox.information(self, "No Workers", f"No {worker_type.name}s to fire.")
            return
        
        current_year = self.operations.get_year()
        event_system = self.operations.get_event_system()
        worker_protection_active = current_year >= 1925
        
        if worker_protection_active:
            if rental.is_on_strike():
                QMessageBox.warning(
                    self, 
                    "Cannot Fire Workers", 
                    f"The Worker Protection Act (1925) prevents firing {worker_type.name}s who are on strike.\n\n"
                    f"You have {rental.quantity} {worker_type.name}s on strike for {rental.strike_turns_remaining} more turn(s).\n\n"
                    f"Before 1925, you could fire striking workers, but this is now illegal."
                )
                return
        
        max_fireable = self.operations.get_max_fireable_workers(player, worker_type.name)
        if max_fireable == 0:
            QMessageBox.information(
                self,
                "Cannot Fire",
                f"You cannot fire any {worker_type.name}s this turn (Worker Protection Act: at most half per turn, or none if on strike)."
            )
            return
        from worker import ENGINEER
        if worker_type.name == ENGINEER.name:
            assigned_count = self.operations.get_player_assigned_trainsets_count(player)
            current_count = rental.quantity
            
            if assigned_count > 0:
                dlg = QDialog(self)
                dlg.setWindowTitle(f"Fire {worker_type.name}s")
                dlg.setGeometry(200, 200, 400, 200)
                layout = QVBoxLayout()
                
                warning_text = f"You have {assigned_count} trainsets assigned to lines.\n"
                warning_text += f"You currently have {current_count} {worker_type.name}s.\n\n"
                if max_fireable < current_count:
                    warning_text += f"Worker Protection Act: you may fire at most {max_fireable} {worker_type.name}(s) per turn (half of your total).\n\n"
                warning_text += f"If you fire {worker_type.name}s, trainsets may be unassigned if there aren't enough {worker_type.name}s remaining."
                layout.addWidget(QLabel(warning_text))
                
                layout.addWidget(QLabel(f"How many {worker_type.name}s to fire?"))
                fire_spin = QSpinBox()
                fire_spin.setMinimum(1)
                fire_spin.setMaximum(max_fireable)
                fire_spin.setValue(1)
                layout.addWidget(fire_spin)
                
                btn_layout = QHBoxLayout()
                fire_btn = QPushButton("Fire")
                cancel_btn = QPushButton("Cancel")
                btn_layout.addWidget(fire_btn)
                btn_layout.addWidget(cancel_btn)
                layout.addLayout(btn_layout)
                
                dlg.setLayout(layout)
                
                def do_fire():
                    quantity_to_fire = fire_spin.value()
                    if quantity_to_fire > max_fireable:
                        QMessageBox.warning(dlg, "Cannot Fire", f"You may fire at most {max_fireable} {worker_type.name}(s) per turn.")
                        return
                    new_count = current_count - quantity_to_fire
                    if new_count < assigned_count:
                        unassign_count = assigned_count - new_count
                        reply = QMessageBox.question(
                            dlg, "Confirm Fire",
                            f"Firing {quantity_to_fire} {worker_type.name}s will leave only {new_count} {worker_type.name}s, "
                            f"but {assigned_count} trainsets are assigned. {unassign_count} trainset(s) will be unassigned.\n\n"
                            f"Continue?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        )
                        if reply != QMessageBox.StandardButton.Yes:
                            return
                    result = self.operations.fire_workers(player, worker_type.name, quantity_to_fire)
                    if not result['success']:
                        QMessageBox.warning(dlg, "Cannot Fire", result['message'])
                        return
                    QMessageBox.information(dlg, "Workers Fired", result['message'])
                    dlg.close()
                    self.DisplayWorkersControls(player)
                    self.DisplayMarketplaceInfo()
                    self.update_status_bar()
                
                fire_btn.clicked.connect(do_fire)
                cancel_btn.clicked.connect(dlg.close)
                dlg.exec()
            else:
                
                dlg = QDialog(self)
                dlg.setWindowTitle(f"Fire {worker_type.name}s")
                dlg.setGeometry(200, 200, 400, 150)
                layout = QVBoxLayout()
                
                layout.addWidget(QLabel(f"You currently have {current_count} {worker_type.name}s."))
                if max_fireable < current_count:
                    layout.addWidget(QLabel(f"Worker Protection Act: you may fire at most {max_fireable} per turn."))
                layout.addWidget(QLabel(f"How many {worker_type.name}s to fire?"))
                fire_spin = QSpinBox()
                fire_spin.setMinimum(1)
                fire_spin.setMaximum(max_fireable)
                fire_spin.setValue(1)
                layout.addWidget(fire_spin)
                
                btn_layout = QHBoxLayout()
                fire_btn = QPushButton("Fire")
                cancel_btn = QPushButton("Cancel")
                btn_layout.addWidget(fire_btn)
                btn_layout.addWidget(cancel_btn)
                layout.addLayout(btn_layout)
                
                dlg.setLayout(layout)
                
                def do_fire():
                    quantity_to_fire = fire_spin.value()
                    result = self.operations.fire_workers(player, worker_type.name, quantity_to_fire)
                    if not result['success']:
                        QMessageBox.warning(dlg, "Cannot Fire", result['message'])
                        return
                    QMessageBox.information(dlg, "Workers Fired", result['message'])
                    dlg.close()
                    self.DisplayWorkersControls(player)
                    self.DisplayMarketplaceInfo()
                    self.update_status_bar()
                
                fire_btn.clicked.connect(do_fire)
                cancel_btn.clicked.connect(dlg.close)
                dlg.exec()
        else:
            
            current_count = rental.quantity
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Fire {worker_type.name}s")
            dlg.setGeometry(200, 200, 400, 150)
            layout = QVBoxLayout()
            
            layout.addWidget(QLabel(f"You currently have {current_count} {worker_type.name}s."))
            if max_fireable < current_count:
                layout.addWidget(QLabel(f"Worker Protection Act: you may fire at most {max_fireable} per turn."))
            layout.addWidget(QLabel(f"How many {worker_type.name}s to fire?"))
            fire_spin = QSpinBox()
            fire_spin.setMinimum(1)
            fire_spin.setMaximum(max_fireable)
            fire_spin.setValue(1)
            layout.addWidget(fire_spin)
            
            btn_layout = QHBoxLayout()
            fire_btn = QPushButton("Fire")
            cancel_btn = QPushButton("Cancel")
            btn_layout.addWidget(fire_btn)
            btn_layout.addWidget(cancel_btn)
            layout.addLayout(btn_layout)
            
            dlg.setLayout(layout)
            
            def do_fire():
                quantity_to_fire = fire_spin.value()
                result = self.operations.fire_workers(player, worker_type.name, quantity_to_fire)
                if not result['success']:
                    QMessageBox.warning(dlg, "Cannot Fire", result['message'])
                    return
                QMessageBox.information(dlg, "Workers Fired", result['message'])
                dlg.close()
                self.DisplayWorkersControls(player)
                self.DisplayMarketplaceInfo()
                self.update_status_bar()
            
            fire_btn.clicked.connect(do_fire)
            cancel_btn.clicked.connect(dlg.close)
            dlg.exec()

    
    def DisplayMapInfo(self):
        try:
            print("=" * 50)
            print("DisplayMapInfo called")
            print(f"  - hasattr(self, 'player_tabs_dict'): {hasattr(self, 'player_tabs_dict')}")
            
            if not hasattr(self, 'player_tabs_dict'):
                print("  - ERROR: player_tabs_dict not found, returning early")
                return
            
            print(f"  - player_tabs_dict keys: {list(self.player_tabs_dict.keys())}")
            print(f"  - player_tabs_dict type: {type(self.player_tabs_dict)}")
            
            all_lines = self.operations.get_map_lines()
            map_info_text = "Map Overview\n\n"
            game_map = self.operations.get_map()
            if hasattr(game_map, 'is_nyc_proper') or hasattr(game_map, 'is_ny_metro'):
                if hasattr(game_map, 'is_nyc_proper') and game_map.is_nyc_proper:
                    map_info_text += f"Map Type: NYC\n"
                elif hasattr(game_map, 'is_ny_metro') and game_map.is_ny_metro:
                    map_info_text += f"Map Type: NY Metropolitan Zone\n"
                else:
                    map_info_text += f"Map Type: Standard\n"
            if hasattr(game_map, 'width') and hasattr(game_map, 'height'):
                map_info_text += f"Size: {game_map.width}x{game_map.height}\n"
            if hasattr(game_map, 'start_year') and hasattr(game_map, 'end_year'):
                start_year = game_map.start_year
                end_year = game_map.end_year
                if start_year is not None and end_year is not None:
                    map_info_text += f"Timeline: {start_year}-{end_year}\n"
                elif hasattr(game_map, 'is_nyc_proper') and game_map.is_nyc_proper:
                    # Ensure end_year is set for NYC maps
                    from map_presets_nyc import get_nyc_timeline
                    nyc_timeline = get_nyc_timeline()
                    game_map.end_year = nyc_timeline['end_year']
                    game_map.start_year = nyc_timeline['start_year']
                    map_info_text += f"Timeline: {game_map.start_year}-{game_map.end_year}\n"
                elif hasattr(game_map, 'is_ny_metro') and game_map.is_ny_metro:
                    # Ensure end_year is set for NY Metro maps
                    from map_presets_ny_metro import get_ny_metro_timeline
                    ny_metro_timeline = get_ny_metro_timeline()
                    game_map.end_year = ny_metro_timeline['end_year']
                    game_map.start_year = ny_metro_timeline['start_year']
                    map_info_text += f"Timeline: {game_map.start_year}-{game_map.end_year}\n"
                else:
                    # Ensure end_year is set for standard maps
                    from map_presets_standard import STANDARD_TIMELINE
                    game_map.end_year = STANDARD_TIMELINE['end_year']
                    game_map.start_year = STANDARD_TIMELINE['start_year']
                    map_info_text += f"Timeline: {game_map.start_year}-{game_map.end_year}\n"
            
            total_population = 0
            if hasattr(game_map, 'population') and game_map.population:
                total_population = sum(pop for pop in game_map.population.values() if pop > 0)
            map_info_text += f"Total Population: {total_population:,}\n"
            
            map_info_text += f"\nTotal Lines: {len(all_lines) if all_lines else 0}\n"
            if all_lines:
                for line_name, line in all_lines.items():
                    owner = self.operations.get_line_owner(line)
                    owner_name = self.operations.get_player_name(owner) if owner else "Unclaimed"
                    num_stations = len(self.operations.get_line_stations(line))
                    num_trainsets = len(self.operations.get_line_vehicles(line))
                    map_info_text += f"\n{line_name} ({owner_name})\n"
                    map_info_text += f"  Stations: {num_stations}\n"
                    map_info_text += f"  Trainsets: {num_trainsets}\n"
            
            print(f"  - self.player_tabs_dict type: {type(self.player_tabs_dict)}")
            print(f"  - self.player_tabs_dict: {self.player_tabs_dict}")
            print(f"  - Iterating over player_tabs_dict...")
            
            if not self.player_tabs_dict:
                print("  - ERROR: self.player_tabs_dict is empty!")
                return
            
            first_key = next(iter(self.player_tabs_dict.keys()))
            first_value = self.player_tabs_dict[first_key]
            print(f"  - First key: {first_key}, type: {type(first_key)}")
            print(f"  - First value type: {type(first_value)}")
            
            if isinstance(first_value, dict):
                print(f"  - Detected nested dict structure")
                for player_id, player_tabs_dict in self.player_tabs_dict.items():
                    print(f"    Processing player_id: {player_id}")
                    map_layout = player_tabs_dict.get('map_layout')
                    map_info_text_widget = player_tabs_dict.get('map_info_text')
                    print(f"    - map_layout: {map_layout}")
                    print(f"    - map_layout is None: {map_layout is None}")
                    print(f"    - map_layout bool: {bool(map_layout)}")
                    print(f"    - map_info_text_widget: {map_info_text_widget}")
                    
                    if map_layout is None:
                        print(f"    - ERROR: map_layout is None for player {player_id}, skipping")
                        continue
                    
                    print(f"    - map_layout is valid, continuing...")
                    
                    print(f"    - map_layout type: {type(map_layout)}")
                    print(f"    - map_layout count: {map_layout.count()}")
                    
                    # Update sidebar map info
                    if map_info_text_widget:
                        map_info_text_widget.setText(map_info_text)
                    
                    # Update station line combo
                    station_line_combo = player_tabs_dict.get('station_line_combo')
                    if station_line_combo:
                        station_line_combo.clear()
                        station_line_combo.addItem("Select line...")
                        # Use the player_id from the player_tabs_dict, not current_player_index
                        # This ensures all players' combos are populated correctly
                        players = self.operations.get_players()
                        current_player = None
                        current_player_id = None
                        for p in players:
                            if self.operations.get_player_id(p) == player_id:
                                current_player = p
                                current_player_id = self.operations.get_player_id(current_player)
                                break
                        if current_player and current_player_id is not None:
                            line_names_set = set()
                            
                            if all_lines:
                                for line_name, line in all_lines.items():
                                    owner = self.operations.get_line_owner(line)
                                    if owner and self.operations.get_player_id(owner) == current_player_id:
                                        if line_name not in line_names_set:
                                            station_line_combo.addItem(line_name, line)
                                            line_names_set.add(line_name)
                            
                            build_queue = self.operations.get_build_queue()
                            for build in build_queue:
                                if build.get('type') == 'line' and build.get('player'):
                                    build_player_id = self.operations.get_player_id(build.get('player'))
                                    if build_player_id == current_player_id:
                                        line_name = build.get('data', {}).get('name')
                                        if line_name and line_name not in line_names_set:
                                            station_line_combo.addItem(line_name, None)
                                            line_names_set.add(line_name)
                    
                    extend_line_combo = player_tabs_dict.get('extend_line_combo')
                    if extend_line_combo:
                        extend_line_combo.clear()
                        extend_line_combo.addItem("Select line...")
                        if current_player:
                            extend_line_names_set = set()
                            if all_lines:
                                for line_name, line in all_lines.items():
                                    owner = self.operations.get_line_owner(line)
                                    if owner and self.operations.get_player_id(owner) == current_player_id:
                                        if line_name not in extend_line_names_set:
                                            extend_line_combo.addItem(line_name, line)
                                            extend_line_names_set.add(line_name)
                            
                            build_queue = self.operations.get_build_queue()
                            for build in build_queue:
                                if build.get('type') == 'line' and build.get('player'):
                                    build_player_id = self.operations.get_player_id(build.get('player'))
                                    if build_player_id == current_player_id:
                                        line_name = build.get('data', {}).get('name')
                                        if line_name and line_name not in extend_line_names_set:
                                            extend_line_combo.addItem(line_name, None)
                                            extend_line_names_set.add(line_name)
                    
                    # Get map tab and ensure it's visible
                    map_tab = player_tabs_dict.get('map_tab')
                    if map_tab is None:
                        continue
                    
                    # map_layout was already retrieved and validated above at line 4263
                    # If it was None, we would have already continued, so it's safe to use here
                    
                    # Check if web_view already exists - if so, just update it instead of recreating
                    existing_web_view = player_tabs_dict.get(f'map_web_view_{player_id}')
                    existing_refresh_btn = player_tabs_dict.get('refresh_map_btn')
                    
                    if not existing_web_view:
                        # Safely clear map_layout
                        try:
                            while map_layout.count():
                                child = map_layout.takeAt(0)
                                if child and child.widget():
                                    widget = child.widget()
                                    widget.setParent(None)  # Remove parent before deleting
                                    widget.deleteLater()
                        except Exception as e:
                            print(f"    - Error clearing map_layout: {e}")
                        
                        # Ensure map_layout is visible and properly set up
                        map_layout.setContentsMargins(5, 5, 5, 5)
                        map_layout.setSpacing(5)
                        
                        refresh_btn = QPushButton("Refresh Map")
                        refresh_btn.clicked.connect(self.DisplayMapInfo)
                        refresh_btn.setMinimumHeight(30)
                        refresh_btn.setMinimumWidth(100)
                        map_layout.addWidget(refresh_btn, 0)  
                        player_tabs_dict['refresh_map_btn'] = refresh_btn
                    else:
                        refresh_btn = existing_refresh_btn
                    
                    map_sidebar = player_tabs_dict.get('map_sidebar')
                    if map_sidebar:
                        map_sidebar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                        map_sidebar.setEnabled(True)
                        map_sidebar.setVisible(True)
                    
                    game_map = self.operations.get_map()
                    # CRITICAL FIX: Initialize osm_map_created based on whether web_view exists
                    # This will be set to True when we create a new web_view or reuse existing one
                    osm_map_created = existing_web_view is not None
                    
                    should_use_osm = hasattr(game_map, 'use_osm') and game_map.use_osm
                    print(f"    - should_use_osm: {should_use_osm}")
                    print(f"    - WEBENGINE_AVAILABLE: {WEBENGINE_AVAILABLE}")
                    
                    if should_use_osm and WEBENGINE_AVAILABLE and QWebEngineView is not None:
                        print(f"    - Entering OSM map creation block")
                        try:
                            # Ensure QApplication exists before creating QWebEngineView
                            app = QApplication.instance()
                            if app is None:
                                print(f"    - ERROR: QApplication instance not found!")
                                raise RuntimeError("QApplication instance required for QWebEngineView")
                            
                            # Reuse existing web_view if it exists and is valid
                            if existing_web_view:
                                print(f"    - Checking existing web_view validity...")
                                web_view = existing_web_view
                                is_valid = False
                                try:
                                    # Check if web_view is still valid
                                    parent = web_view.parent()
                                    page = self._safe_get_webview_page(web_view)
                                    is_valid = (parent is not None) and (page is not None)
                                except (RuntimeError, AttributeError):
                                    is_valid = False
                                
                                if not is_valid:
                                    print(f"    - WARNING: existing web_view is invalid, will recreate")
                                    existing_web_view = None
                                    web_view = None
                                else:
                                    print(f"    - Reusing valid existing web_view")
                                    # Generate HTML synchronously to ensure it's set before continuing
                                    html_content = self._generate_osm_map_html(game_map)
                                    base_url = QUrl("https://example.com/")
                                    def setup_after_load(success):
                                        if success:
                                            try:
                                                self._setup_map_event_handlers(web_view, player_id, player_tabs_dict)
                                            except Exception as e:
                                                print(f"    - Error in setup_after_load: {e}")
                                    try:
                                        # Safely disconnect existing connections
                                        try:
                                            receivers = web_view.receivers(web_view.loadFinished)
                                            if receivers > 0:
                                                web_view.loadFinished.disconnect()
                                        except Exception:
                                            pass
                                        web_view.loadFinished.connect(setup_after_load)
                                        web_view.setHtml(html_content, base_url)
                                        osm_map_created = True
                                        print(f"    - Updated existing web_view HTML")
                                    except Exception as e:
                                        print(f"    - Error updating existing web_view: {e}, will recreate")
                                        existing_web_view = None
                                        web_view = None
                            
                            if not existing_web_view or web_view is None:
                                print(f"    - Creating new web_view")
                                
                                print(f"    - Creating QWebEngineView...")
                                # Create QWebEngineView with parent widget to ensure proper lifecycle
                                # Use map_tab as parent to ensure it's properly managed
                                web_view = QWebEngineView(map_tab)  # Parent to map_tab
                                print(f"    - QWebEngineView created: {web_view}")
                                
                                # Set size policy BEFORE adding to layout
                                web_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                                web_view.setMinimumSize(400, 300)
                                
                                # Add to layout FIRST before setting HTML (prevents segfaults)
                                print(f"    - Adding web_view to map_layout...")
                                print(f"      - map_layout before: count={map_layout.count()}")
                                map_layout.addWidget(web_view, 1)  # Stretch factor of 1
                                print(f"      - map_layout after: count={map_layout.count()}")
                                
                                # Store web_view reference for later use
                                player_tabs_dict[f'map_web_view_{player_id}'] = web_view
                                print(f"    - Stored web_view in player_tabs_dict")
                                
                                # Generate HTML synchronously to ensure it's set before continuing
                                # Add processEvents calls during generation to prevent UI freeze
                                print(f"    - Generating HTML content...")
                                for _ in range(3):
                                    QApplication.processEvents()
                                html_content = self._generate_osm_map_html(game_map)
                                print(f"    - HTML content generated, length: {len(html_content)} chars")
                                for _ in range(3):
                                    QApplication.processEvents()
                                
                                base_url = QUrl("https://example.com/")
                                print(f"    - Setting HTML with base_url: {base_url}")
                                
                                def setup_after_initial_load(success):
                                    if success:
                                        try:
                                            self._setup_map_event_handlers(web_view, player_id, player_tabs_dict)
                                        except Exception as e:
                                            print(f"    - Error in setup_after_initial_load: {e}")
                                
                                # Defer setHtml slightly to ensure widget is fully initialized
                                # Capture variables in closure
                                html_content_capture = html_content
                                base_url_capture = base_url
                                def deferred_set_html():
                                    try:
                                        web_view.loadFinished.connect(setup_after_initial_load)
                                        web_view.setHtml(html_content_capture, base_url_capture)
                                        print(f"    - HTML set successfully")
                                    except Exception as e:
                                        print(f"    - Error setting HTML: {e}")
                                
                                from PyQt6.QtCore import QTimer
                                QTimer.singleShot(50, deferred_set_html)  # 50ms delay
                                
                                osm_map_created = True
                                print(f"    - SUCCESS: Map web view created for player {player_id}")
                        except Exception as e:
                            import traceback
                            print(f"    - ERROR creating QWebEngineView: {e}")
                            print(traceback.format_exc())
                            # Fall back to canvas rendering
                            osm_map_created = False
                            print(f"    - Falling back to canvas rendering")
                    else:
                        print(f"    - Skipping OSM map creation (should_use_osm={should_use_osm}, WEBENGINE_AVAILABLE={WEBENGINE_AVAILABLE})")
                        # Fallback: use QTextBrowser with static tiles when WebEngine unavailable
                        if should_use_osm:
                            try:
                                fallback_html = self._generate_simple_map_html_no_webengine(game_map)
                                fallback_browser = QTextBrowser(map_tab)
                                fallback_browser.setOpenExternalLinks(True)
                                fallback_browser.setHtml(fallback_html)
                                fallback_browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                                fallback_browser.setMinimumSize(400, 300)
                                map_layout.addWidget(fallback_browser, 1)
                                osm_map_created = True
                                print(f"    - Using QTextBrowser fallback (static map, no interactivity)")
                            except Exception as e:
                                import traceback
                                print(f"    - Fallback map failed: {e}")
                                print(traceback.format_exc())
                    
                    if osm_map_created or existing_web_view:
                        web_view = existing_web_view if existing_web_view else player_tabs_dict.get(f'map_web_view_{player_id}')
                        if web_view:
                            if hasattr(self, 'current_player_index') and self.current_player_index is not None:
                                try:
                                    first_player = self.operations.get_current_player(self.current_player_index)
                                    if first_player and player_id == self.operations.get_player_id(first_player):
                                        self.map_web_view = web_view
                                except Exception:
                                    pass
                    else:
                        # OSM map should have been created but wasn't - show error
                        error_label = QLabel("Error: Failed to load map. Please check console for details.")
                        error_label.setStyleSheet("color: red; font-weight: bold; padding: 20px;")
                        map_layout.addWidget(error_label)
                    
                    print(f"    Finished processing player {player_id}")
            
            print("=" * 50)
        except Exception as e:
            import traceback
            print(f"ERROR in DisplayMapInfo: {e}")
            print(traceback.format_exc())
            raise
    
    def _connect_map_sidebar_buttons(self, player_id, player_tabs_dict):
        create_line_btn = player_tabs_dict.get('create_line_btn')
        finish_line_btn = player_tabs_dict.get('finish_line_btn')
        cancel_line_btn = player_tabs_dict.get('cancel_line_btn')
        
        if create_line_btn:
            receivers = create_line_btn.receivers(create_line_btn.clicked)
            if receivers > 0:
                create_line_btn.clicked.disconnect()
            
            def start_line_creation():
                # Use the player_id for this specific button, not current_player_index
                # This ensures each player's button works correctly
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    QMessageBox.warning(self, "Error", "Player tabs not found.")
                    return
                
                # Get the player object for this player_id
                players = self.operations.get_players()
                current_player = None
                for p in players:
                    if self.operations.get_player_id(p) == player_id:
                        current_player = p
                        break
                
                if not current_player:
                    QMessageBox.warning(self, "Error", "No active player.")
                    return
                
                line_name_widget = current_player_tabs_dict.get('line_name_input')
                if not line_name_widget:
                    QMessageBox.warning(self, "Error", "Line name input not found.")
                    return
                
                from PyQt6.QtWidgets import QLineEdit
                if not isinstance(line_name_widget, QLineEdit):
                    QMessageBox.warning(self, "Error", "Line name input is not a valid widget.")
                    return
                
                line_name = line_name_widget.text()
                if line_name is None:
                    line_name = ""
                line_name = line_name.strip()
                
                if not line_name:
                    QMessageBox.warning(self, "Error", "Please enter a line name first.")
                    return
                
                web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                if not web_view:
                    web_view = self.map_web_view
                
                page = self._safe_get_webview_page(web_view)
                if page:
                    page.runJavaScript("window.startLineCreation();")
                    print(f"Started line creation for player {player_id}: {line_name}")
                else:
                    QMessageBox.warning(self, "Error", "Map is not ready. Please wait for map to load.")
                    return
                
                if create_line_btn:
                    create_line_btn.setEnabled(False)
                if finish_line_btn:
                    finish_line_btn.setEnabled(True)
                if cancel_line_btn:
                    cancel_line_btn.setEnabled(True)
            
            create_line_btn.clicked.connect(start_line_creation)
        
        if cancel_line_btn:
            receivers = cancel_line_btn.receivers(cancel_line_btn.clicked)
            if receivers > 0:
                cancel_line_btn.clicked.disconnect()
            
            def cancel_line_creation():
                # Use the player_id for this specific button
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    return
                
                web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                if not web_view:
                    web_view = self.map_web_view
                
                page = self._safe_get_webview_page(web_view)
                if page:
                    page.runJavaScript("window.cancelLineCreation();")
                
                line_points_label = current_player_tabs_dict.get('line_points_label')
                if create_line_btn:
                    create_line_btn.setEnabled(True)
                if finish_line_btn:
                    finish_line_btn.setEnabled(False)
                if cancel_line_btn:
                    cancel_line_btn.setEnabled(False)
                if line_points_label:
                    line_points_label.setText("Points: 0")
            
            cancel_line_btn.clicked.connect(cancel_line_creation)
        
        if finish_line_btn:
            receivers = finish_line_btn.receivers(finish_line_btn.clicked)
            if receivers > 0:
                finish_line_btn.clicked.disconnect()
            
            def finish_line_creation():
                # Use the player_id for this specific button
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    QMessageBox.warning(self, "Error", "Player tabs not found.")
                    return
                
                # Get the player object for this player_id
                players = self.operations.get_players()
                current_player = None
                for p in players:
                    if self.operations.get_player_id(p) == player_id:
                        current_player = p
                        break
                
                if not current_player:
                    QMessageBox.warning(self, "Error", "No active player.")
                    return
                
                line_name_widget = current_player_tabs_dict.get('line_name_input')
                line_name = ""
                if line_name_widget:
                    line_name = line_name_widget.text().strip()
                
                if not line_name:
                    QMessageBox.warning(self, "Error", "Please enter a line name first.")
                    return
                
                current_web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                if not current_web_view:
                    current_web_view = self.map_web_view
                
                page = self._safe_get_webview_page(current_web_view)
                if not page:
                    QMessageBox.warning(self, "Error", "Map is not ready.")
                    return
                
                def handle_points(points_json):
                    import json
                    if not points_json:
                        QMessageBox.warning(self, "Error", "No points received from map.")
                        return
                    
                    latlon_points = json.loads(points_json) if isinstance(points_json, str) else points_json
                    if not latlon_points or len(latlon_points) < 2:
                        QMessageBox.warning(self, "Error", "A line needs at least 2 points.")
                        return
                    
                    grid_points = self.operations.convert_latlon_points_to_grid(latlon_points)
                    if not grid_points or len(grid_points) < 2:
                        QMessageBox.warning(self, "Error", "Failed to convert coordinates.")
                        return
                    
                    # Ask user to choose between train line, ferry line, and bus line
                    line_type_dlg = QDialog(self)
                    line_type_dlg.setWindowTitle("Select Line Type")
                    line_type_dlg.setGeometry(300, 300, 300, 150)
                    line_type_layout = QVBoxLayout()
                    line_type_layout.addWidget(QLabel("What type of line do you want to create?"))
                    
                    line_type_combo = QComboBox()
                    line_type_combo.addItems(['Train Line', 'Ferry Line', 'Bus Line'])
                    line_type_layout.addWidget(line_type_combo)
                    
                    line_type_btn_layout = QHBoxLayout()
                    line_type_ok_btn = QPushButton("OK")
                    line_type_cancel_btn = QPushButton("Cancel")
                    line_type_ok_btn.clicked.connect(line_type_dlg.accept)
                    line_type_cancel_btn.clicked.connect(line_type_dlg.reject)
                    line_type_btn_layout.addWidget(line_type_ok_btn)
                    line_type_btn_layout.addWidget(line_type_cancel_btn)
                    line_type_layout.addLayout(line_type_btn_layout)
                    line_type_dlg.setLayout(line_type_layout)
                    
                    if line_type_dlg.exec() != QDialog.DialogCode.Accepted:
                        return
                    
                    selected_type = line_type_combo.currentText()
                    is_ferry_line = (selected_type == 'Ferry Line')
                    is_bus_line = (selected_type == 'Bus Line')
                    
                    # For ferry lines, skip elevation selection (they're always on water)
                    if is_ferry_line:
                        elevation_levels = ['water'] * (len(grid_points) - 1)
                    else:
                        # Ask for elevation levels with dropdowns
                        required_elevations = len(grid_points) - 1
                        elev_dlg = QDialog(self)
                        elev_dlg.setWindowTitle("Select Elevation Levels")
                        elev_dlg.setGeometry(300, 300, 400, min(600, 100 + required_elevations * 50))
                        elev_layout = QVBoxLayout()
                        elev_layout.addWidget(QLabel(f"Select elevation level for each of {required_elevations} segments:"))
                        elev_layout.addWidget(QLabel("Note: Cannot use 'street' on water"))
                        
                        from PyQt6.QtWidgets import QScrollArea, QWidget as QWidget2
                        elev_scroll = QScrollArea()
                        elev_scroll.setWidgetResizable(True)
                        elev_widget = QWidget2()
                        elev_widget_layout = QVBoxLayout()
                        elev_widget.setLayout(elev_widget_layout)
                        elev_scroll.setWidget(elev_widget)
                        elev_layout.addWidget(elev_scroll)
                        
                        elevation_combos = []
                        for i in range(required_elevations):
                            segment_label = QLabel(f"Segment {i+1} (point {i} → {i+1}):")
                            elev_widget_layout.addWidget(segment_label)
                            combo = QComboBox()
                            combo.addItems(['street', 'above_ground', 'underground'])
                            combo.setCurrentText('street')  # Default
                            elev_widget_layout.addWidget(combo)
                            elevation_combos.append(combo)
                        
                        elev_btn_layout = QHBoxLayout()
                        elev_ok_btn = QPushButton("OK")
                        elev_cancel_btn = QPushButton("Cancel")
                        elev_ok_btn.clicked.connect(elev_dlg.accept)
                        elev_cancel_btn.clicked.connect(elev_dlg.reject)
                        elev_btn_layout.addWidget(elev_ok_btn)
                        elev_btn_layout.addWidget(elev_cancel_btn)
                        elev_layout.addLayout(elev_btn_layout)
                        elev_dlg.setLayout(elev_layout)
                        
                        if elev_dlg.exec() != QDialog.DialogCode.Accepted:
                            return
                        
                        # Get elevation levels from combo boxes
                        elevation_levels = [combo.currentText() for combo in elevation_combos]
                    
                    try:
                        cost_info = self.operations.calculate_line_cost(
                            grid_points,
                            elevation_levels,
                            is_bus_line=is_bus_line,
                            is_ferry_line=is_ferry_line
                        )
                    except Exception as e:
                        QMessageBox.warning(self, "Error", str(e))
                        return
                    
                    reply = QMessageBox.question(self, "Confirm Line Creation",
                                                 f"Creating this line will cost ${cost_info['cost']} (distance: {cost_info['length']:.2f} units). Proceed?",
                                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                    
                    # CRITICAL: Process events MANY times after message box closes to ensure UI is fully responsive
                    # The message box cleanup can take time, and we need to ensure it's completely done
                    # before starting any potentially blocking work
                    for _ in range(10):
                        QApplication.processEvents()
                    
                    result = None
                    error_occurred = False
                    error_message = ""
                    line_creation_timeout_timer = None
                    
                    def handle_line_creation_result(res=None, err_occurred=False, err_msg=""):
                        nonlocal line_creation_timeout_timer
                        if line_creation_timeout_timer is not None:
                            line_creation_timeout_timer.stop()
                            line_creation_timeout_timer = None
                        # CRITICAL: Ensure UI is responsive before showing any dialogs
                        QApplication.processEvents()
                        
                        if err_occurred:
                            QMessageBox.warning(self, "Error", f"Failed to create line: {err_msg}")
                            QApplication.processEvents()
                            return
                        if not res:
                            QMessageBox.warning(self, "Error", "Failed to create line.")
                            QApplication.processEvents()
                            return
                        
                        # Check if validation failed (for ferry or bus lines)
                        if res.get('success') == False:
                            error_message = res.get('error_message', 'Unknown error')
                            if is_ferry_line:
                                QMessageBox.warning(self, "Cannot Create Ferry Line", error_message)
                            elif is_bus_line:
                                QMessageBox.warning(self, "Cannot Create Bus Line", error_message)
                            else:
                                QMessageBox.warning(self, "Cannot Create Line", error_message)
                            QApplication.processEvents()
                            return
                        
                        if is_ferry_line:
                            line_type_str = "Ferry line"
                        elif is_bus_line:
                            line_type_str = "Bus line"
                        else:
                            line_type_str = "Line"
                        
                        # Show success message
                        QMessageBox.information(self, f"{line_type_str} Created", 
                                                 f"Line '{line_name}' is being built. Estimated completion: {res['build_time']} turns.")
                        
                        # CRITICAL: Process events MANY times after success message box closes
                        # The message box cleanup can take time, and we need to ensure it's completely done
                        for _ in range(10):
                            QApplication.processEvents()
                        
                        # Update UI elements immediately
                        finish_web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                        if not finish_web_view:
                            finish_web_view = self.map_web_view
                        
                        finish_page = self._safe_get_webview_page(finish_web_view)
                        if finish_page:
                            finish_page.runJavaScript("window.cancelLineCreation();")
                        
                        create_line_btn = current_player_tabs_dict.get('create_line_btn')
                        finish_line_btn = current_player_tabs_dict.get('finish_line_btn')
                        cancel_line_btn = current_player_tabs_dict.get('cancel_line_btn')
                        line_points_label = current_player_tabs_dict.get('line_points_label')
                        
                        if create_line_btn:
                            create_line_btn.setEnabled(True)
                        if finish_line_btn:
                            finish_line_btn.setEnabled(False)
                        if cancel_line_btn:
                            cancel_line_btn.setEnabled(False)
                        if line_name_widget:
                            line_name_widget.clear()
                        if line_points_label:
                            line_points_label.setText("Points: 0")
                        
                        # Allow UI to update after button state changes
                        # Process events multiple times to ensure all UI updates are complete
                        for _ in range(10):
                            QApplication.processEvents()
                        
                        # OPTIMIZATION: Defer map refresh significantly to prevent UI freeze after confirmation
                        # Use much longer delay to ensure message box is fully closed and UI is responsive
                        # This allows the user to interact with the UI before the heavy map refresh happens
                        # CRITICAL: Map refresh can be very slow (generates HTML, processes all lines, etc.)
                        # Defer it significantly to prevent blocking the UI thread
                        QTimer.singleShot(3000, self.DisplayMapInfo)  # 3000ms delay - ensures UI is fully responsive before heavy operation
                    
                    # Run line creation in a background thread so UI never freezes (max 15s effective limit)
                    def start_line_creation_worker():
                        nonlocal line_creation_timeout_timer
                        for _ in range(5):
                            QApplication.processEvents()
                        worker = LineCreationWorker(
                            self.operations, line_name, grid_points, current_player, elevation_levels,
                            is_ferry_line, is_bus_line
                        )
                        thread = QThread()
                        worker.moveToThread(thread)
                        thread.started.connect(worker.run)
                        def on_worker_finished(res, err_occurred, err_msg):
                            handle_line_creation_result(res, err_occurred, err_msg)
                            thread.quit()
                        worker.finished.connect(on_worker_finished)
                        worker.finished.connect(thread.quit)
                        # Keep references so thread/worker are not garbage-collected
                        self._line_creation_thread = thread
                        self._line_creation_worker = worker
                        thread.finished.connect(lambda: (setattr(self, '_line_creation_thread', None), setattr(self, '_line_creation_worker', None)))
                        thread.start()
                        # 15-second timeout: if work takes longer, show message (work continues in background)
                        def on_timeout():
                            nonlocal line_creation_timeout_timer
                            line_creation_timeout_timer = None
                            QMessageBox.information(
                                self, "Still Working",
                                "Line creation is taking longer than expected (max 15 seconds). You can continue using the app; you'll be notified when it finishes."
                            )
                        line_creation_timeout_timer = QTimer(self)
                        line_creation_timeout_timer.setSingleShot(True)
                        line_creation_timeout_timer.timeout.connect(on_timeout)
                        line_creation_timeout_timer.start(LINE_CREATION_MAX_MS)
                    
                    QTimer.singleShot(500, start_line_creation_worker)  
                
                page = self._safe_get_webview_page(current_web_view)
                if page:
                    page.runJavaScript("JSON.stringify(window.finishLineCreation());", handle_points)
                else:
                    QMessageBox.warning(self, "Error", "Map is not ready.")
                    return
            
            finish_line_btn.clicked.connect(finish_line_creation)
        
        extend_line_btn = player_tabs_dict.get('extend_line_btn')
        if extend_line_btn:
            receivers = extend_line_btn.receivers(extend_line_btn.clicked)
            if receivers > 0:
                extend_line_btn.clicked.disconnect()
            
            def start_extend_line():
                current_player = self.operations.get_current_player(self.current_player_index)
                if not current_player:
                    QMessageBox.warning(self, "Error", "No active player.")
                    return
                
                current_player_id = self.operations.get_player_id(current_player)
                current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
                if not current_player_tabs_dict:
                    QMessageBox.warning(self, "Error", "Player tabs not found.")
                    return
                
                extend_line_combo = current_player_tabs_dict.get('extend_line_combo')
                if not extend_line_combo:
                    QMessageBox.warning(self, "Error", "Extend line combo not found.")
                    return
                
                selected_line = extend_line_combo.currentText()
                if not selected_line or selected_line == "Select line...":
                    QMessageBox.warning(self, "Error", "Please select a line to extend first.")
                    return
                
                web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
                if not web_view:
                    web_view = self.map_web_view
                
                page = self._safe_get_webview_page(web_view)
                if page:
                    js_code = f"window.startLineExtension('{selected_line}');"
                    page.runJavaScript(js_code)
                    
                    finish_extend_btn = current_player_tabs_dict.get('finish_extend_btn')
                    cancel_extend_btn = current_player_tabs_dict.get('cancel_extend_btn')
                    extend_line_btn = current_player_tabs_dict.get('extend_line_btn')
                    
                    if extend_line_btn:
                        extend_line_btn.setEnabled(False)
                    if finish_extend_btn:
                        finish_extend_btn.setEnabled(True)
                    if cancel_extend_btn:
                        cancel_extend_btn.setEnabled(True)
                else:
                    QMessageBox.warning(self, "Error", "Map is not ready.")
            
            extend_line_btn.clicked.connect(start_extend_line)
            
            finish_extend_btn = player_tabs_dict.get('finish_extend_btn')
            cancel_extend_btn = player_tabs_dict.get('cancel_extend_btn')
            
            if finish_extend_btn:
                receivers = finish_extend_btn.receivers(finish_extend_btn.clicked)
                if receivers > 0:
                    finish_extend_btn.clicked.disconnect()
                
                def finish_extend_line():
                    current_player = self.operations.get_current_player(self.current_player_index)
                    if not current_player:
                        QMessageBox.warning(self, "Error", "No active player.")
                        return
                    
                    current_player_id = self.operations.get_player_id(current_player)
                    current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
                    if not current_player_tabs_dict:
                        QMessageBox.warning(self, "Error", "Player tabs not found.")
                        return
                    
                    extend_line_combo = current_player_tabs_dict.get('extend_line_combo')
                    if not extend_line_combo:
                        return
                    
                    selected_line = extend_line_combo.currentText()
                    if not selected_line or selected_line == "Select line...":
                        QMessageBox.warning(self, "Error", "Please select a line to extend.")
                        return
                    
                    current_web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
                    if not current_web_view:
                        current_web_view = self.map_web_view
                    
                    page = self._safe_get_webview_page(current_web_view)
                    if not page:
                        QMessageBox.warning(self, "Error", "Map is not ready.")
                        return
                    
                    def handle_extend_points(points_json):
                        import json
                        if not points_json:
                            QMessageBox.warning(self, "Error", "No points received from map.")
                            return
                        
                        latlon_points = json.loads(points_json) if isinstance(points_json, str) else points_json
                        if not latlon_points or len(latlon_points) < 1:
                            QMessageBox.warning(self, "Error", "At least one point is required to extend a line.")
                            return
                        
                        grid_points = self.operations.convert_latlon_points_to_grid(latlon_points)
                        if not grid_points or len(grid_points) < 1:
                            QMessageBox.warning(self, "Error", "Failed to convert coordinates.")
                            return
                        
                        # Ask for elevation levels with dropdowns
                        required_elevations = len(grid_points)
                        elev_dlg = QDialog(self)
                        elev_dlg.setWindowTitle("Select Elevation Levels")
                        elev_dlg.setGeometry(300, 300, 400, min(600, 100 + required_elevations * 50))
                        elev_layout = QVBoxLayout()
                        elev_layout.addWidget(QLabel(f"Select elevation level for each of {required_elevations} points:"))
                        elev_layout.addWidget(QLabel("Note: Cannot use 'street' on water"))
                        
                        from PyQt6.QtWidgets import QScrollArea, QWidget as QWidget2
                        elev_scroll = QScrollArea()
                        elev_scroll.setWidgetResizable(True)
                        elev_widget = QWidget2()
                        elev_widget_layout = QVBoxLayout()
                        elev_widget.setLayout(elev_widget_layout)
                        elev_scroll.setWidget(elev_widget)
                        elev_layout.addWidget(elev_scroll)
                        
                        elevation_combos = []
                        for i in range(required_elevations):
                            point_label = QLabel(f"Point {i+1} ({grid_points[i][0]:.1f}, {grid_points[i][1]:.1f}):")
                            elev_widget_layout.addWidget(point_label)
                            combo = QComboBox()
                            combo.addItems(['street', 'above_ground', 'underground'])
                            combo.setCurrentText('street')  # Default
                            elev_widget_layout.addWidget(combo)
                            elevation_combos.append(combo)
                        
                        elev_btn_layout = QHBoxLayout()
                        elev_ok_btn = QPushButton("OK")
                        elev_cancel_btn = QPushButton("Cancel")
                        elev_ok_btn.clicked.connect(elev_dlg.accept)
                        elev_cancel_btn.clicked.connect(elev_dlg.reject)
                        elev_btn_layout.addWidget(elev_ok_btn)
                        elev_btn_layout.addWidget(elev_cancel_btn)
                        elev_layout.addLayout(elev_btn_layout)
                        elev_dlg.setLayout(elev_layout)
                        
                        if elev_dlg.exec() != QDialog.DialogCode.Accepted:
                            return
                        
                        # Get elevation levels from combo boxes
                        elevation_levels = [combo.currentText() for combo in elevation_combos]
                        
                        extend_from_end = True
                        try:
                            cost_info = self.operations.calculate_extend_cost(selected_line, grid_points, extend_from_end, elevation_levels)
                        except Exception as e:
                            QMessageBox.warning(self, "Error", str(e))
                            return
                        
                        if not cost_info:
                            QMessageBox.warning(self, "Error", "Failed to calculate extension cost.")
                            return
                        
                        reply = QMessageBox.question(self, "Confirm Line Extension",
                                                     f"Extending this line will cost ${cost_info['cost']} (distance: {cost_info['length']:.2f} units). Proceed?",
                                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        if reply != QMessageBox.StandardButton.Yes:
                            return
                        
                        result = self.operations.extend_line_complete(selected_line, current_player, grid_points, extend_from_end, elevation_levels)
                        if not result:
                            QMessageBox.warning(self, "Error", "Failed to extend line.")
                            return
                        
                        QMessageBox.information(self, "Line Extended", 
                                                 f"Line '{selected_line}' extension is being built. Estimated completion: {result['build_time']} turns.")
                        
                        finish_web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
                        if not finish_web_view:
                            finish_web_view = self.map_web_view
                        
                        finish_page = self._safe_get_webview_page(finish_web_view)
                        if finish_page:
                            finish_page.runJavaScript("window.cancelLineExtension();")
                        
                        extend_line_btn = current_player_tabs_dict.get('extend_line_btn')
                        finish_extend_btn = current_player_tabs_dict.get('finish_extend_btn')
                        cancel_extend_btn = current_player_tabs_dict.get('cancel_extend_btn')
                        extend_line_points_label = current_player_tabs_dict.get('extend_line_points_label')
                        
                        if extend_line_btn:
                            extend_line_btn.setEnabled(True)
                        if finish_extend_btn:
                            finish_extend_btn.setEnabled(False)
                        if cancel_extend_btn:
                            cancel_extend_btn.setEnabled(False)
                        if extend_line_points_label:
                            extend_line_points_label.setText("Points: 0")
                        
                        # OPTIMIZATION: Defer map refresh to prevent UI freeze
                        QTimer.singleShot(200, self.DisplayMapInfo)
                    
                    extend_page = self._safe_get_webview_page(current_web_view)
                    if extend_page:
                        extend_page.runJavaScript("JSON.stringify(window.finishLineExtension());", handle_extend_points)
                    else:
                        QMessageBox.warning(self, "Error", "Map is not ready.")
                        return
                
                finish_extend_btn.clicked.connect(finish_extend_line)
            
            if cancel_extend_btn:
                receivers = cancel_extend_btn.receivers(cancel_extend_btn.clicked)
                if receivers > 0:
                    cancel_extend_btn.clicked.disconnect()
                
                def cancel_extend_line():
                    current_player = self.operations.get_current_player(self.current_player_index)
                    if not current_player:
                        return
                    
                    current_player_id = self.operations.get_player_id(current_player)
                    current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
                    if not current_player_tabs_dict:
                        return
                    
                    web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
                    if not web_view:
                        web_view = self.map_web_view
                    
                    page = self._safe_get_webview_page(web_view)
                    if page:
                        page.runJavaScript("window.cancelLineExtension();")
                    
                    extend_line_btn = current_player_tabs_dict.get('extend_line_btn')
                    finish_extend_btn = current_player_tabs_dict.get('finish_extend_btn')
                    cancel_extend_btn = current_player_tabs_dict.get('cancel_extend_btn')
                    extend_line_points_label = current_player_tabs_dict.get('extend_line_points_label')
                    
                    if extend_line_btn:
                        extend_line_btn.setEnabled(True)
                    if finish_extend_btn:
                        finish_extend_btn.setEnabled(False)
                    if cancel_extend_btn:
                        cancel_extend_btn.setEnabled(False)
                    if extend_line_points_label:
                        extend_line_points_label.setText("Points: 0")
                
                cancel_extend_btn.clicked.connect(cancel_extend_line)
        
        create_station_btn = player_tabs_dict.get('create_station_btn')
        station_name_input = player_tabs_dict.get('station_name_input')
        station_line_combo = player_tabs_dict.get('station_line_combo')
        
        if create_station_btn and station_name_input and station_line_combo:
            receivers = create_station_btn.receivers(create_station_btn.clicked)
            if receivers > 0:
                create_station_btn.clicked.disconnect()
            
            def start_station_creation():
                # Use the player_id for this specific button, not current_player_index
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    QMessageBox.warning(self, "Error", "Player tabs not found.")
                    return
                
                # Get the player object for this player_id
                players = self.operations.get_players()
                current_player = None
                for p in players:
                    if self.operations.get_player_id(p) == player_id:
                        current_player = p
                        break
                
                if not current_player:
                    QMessageBox.warning(self, "Error", "No active player.")
                    return
                
                # Get widgets from the current player's tabs dict
                station_name_input_widget = current_player_tabs_dict.get('station_name_input')
                station_line_combo_widget = current_player_tabs_dict.get('station_line_combo')
                
                station_name = station_name_input_widget.text().strip() if station_name_input_widget else ""
                selected_line = station_line_combo_widget.currentText() if station_line_combo_widget else ""
                
                if not station_name:
                    QMessageBox.warning(self, "Error", "Please enter a station name.")
                    return
                
                if not selected_line or selected_line == "Select line...":
                    QMessageBox.warning(self, "Error", "Please select a line.")
                    return
                
                # Store station creation state in Python (synchronous)
                if not hasattr(self, '_station_creation_state'):
                    self._station_creation_state = {}
                self._station_creation_state[player_id] = {
                    'active': True,
                    'line_name': selected_line,
                    'station_name': station_name
                }
                
                web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                if not web_view:
                    web_view = getattr(self, 'map_web_view', None)
                
                # Safety check to prevent segfault
                page = self._safe_get_webview_page(web_view)
                if page:
                    try:
                        # Escape the line name for JavaScript
                        escaped_line = selected_line.replace("'", "\\'").replace('"', '\\"')
                        js_code = f"window.startStationCreationWithLine('{escaped_line}');"
                        page.runJavaScript(js_code)
                        QMessageBox.information(self, "Station Creation Mode", 
                                                f"Click on a point on line '{selected_line}' to place station '{station_name}'.")
                    except Exception as e:
                        QMessageBox.warning(self, "Error", f"Failed to start station creation: {str(e)}")
                else:
                    QMessageBox.warning(self, "Error", "Map is not ready. Please wait for the map to load.")
                    return
            
            create_station_btn.clicked.connect(start_station_creation)
    
    def _safe_get_webview_page(self, web_view):
        """Safely get the page from a web_view, returning None if not available.
        Prevents segfaults from accessing deleted or invalid pages."""
        if not web_view:
            return None
        try:
            page = web_view.page()
            if page is None:
                return None
            # Additional check: try to access a safe property to ensure page is valid
            _ = page.url()  # This will raise RuntimeError if page is deleted
            return page
        except (RuntimeError, AttributeError, TypeError):
            return None
    
    def _setup_map_event_handlers(self, web_view, player_id, player_tabs_dict):
        # Safety check to prevent segfault
        page = self._safe_get_webview_page(web_view)
        if page is None:
            return
        
        create_line_btn = player_tabs_dict.get('create_line_btn')
        finish_line_btn = player_tabs_dict.get('finish_line_btn')
        cancel_line_btn = player_tabs_dict.get('cancel_line_btn')
        line_points_label = player_tabs_dict.get('line_points_label')
        
        if create_line_btn:
            receivers = create_line_btn.receivers(create_line_btn.clicked)
            if receivers > 0:
                create_line_btn.clicked.disconnect()
            
            def start_line_creation():
                # Use the player_id for this specific button, not current_player_index
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    QMessageBox.warning(self, "Error", "Player tabs not found.")
                    return
                
                # Get the player object for this player_id
                players = self.operations.get_players()
                current_player = None
                for p in players:
                    if self.operations.get_player_id(p) == player_id:
                        current_player = p
                        break
                
                if not current_player:
                    QMessageBox.warning(self, "Error", "No active player.")
                    return
                
                line_name_widget = current_player_tabs_dict.get('line_name_input')
                if not line_name_widget:
                    QMessageBox.warning(self, "Error", "Line name input not found.")
                    return
                
                from PyQt6.QtWidgets import QLineEdit
                if not isinstance(line_name_widget, QLineEdit):
                    QMessageBox.warning(self, "Error", "Line name input is not a valid widget.")
                    return
                
                line_name = line_name_widget.text()
                if line_name is None:
                    line_name = ""
                line_name = line_name.strip()
                
                if not line_name:
                    QMessageBox.warning(self, "Error", "Please enter a line name first.")
                    return
                
                start_web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                if not start_web_view:
                    start_web_view = getattr(self, 'map_web_view', None)
                
                # Safety check to prevent segfault
                if start_web_view:
                    try:
                        page = start_web_view.page()
                        if page is not None:
                            page.runJavaScript("window.startLineCreation();")
                            print(f"Started line creation for: {line_name}")
                        else:
                            QMessageBox.warning(self, "Error", "Map page is not ready. Please wait for map to load.")
                    except (RuntimeError, AttributeError) as e:
                        QMessageBox.warning(self, "Error", f"Map is not ready: {e}. Please wait for map to load.")
                else:
                    QMessageBox.warning(self, "Error", "Map is not ready. Please wait for map to load.")
                    return
                
                if create_line_btn:
                    create_line_btn.setEnabled(False)
                if finish_line_btn:
                    finish_line_btn.setEnabled(True)
                if cancel_line_btn:
                    cancel_line_btn.setEnabled(True)
            
            create_line_btn.clicked.connect(start_line_creation)
            print(f"Connected Start New Line button for player {player_id}")
        
        if cancel_line_btn:
            receivers = cancel_line_btn.receivers(cancel_line_btn.clicked)
            if receivers > 0:
                cancel_line_btn.clicked.disconnect()
                
                def cancel_line_creation():
                    current_player = self.operations.get_current_player(self.current_player_index)
                    if current_player:
                        current_player_id = self.operations.get_player_id(current_player)
                        current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
                        if current_player_tabs_dict:
                            cancel_web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
                            if not cancel_web_view:
                                cancel_web_view = self.map_web_view
                            if cancel_web_view and cancel_web_view.page():
                                cancel_page = self._safe_get_webview_page(cancel_web_view)
                                if cancel_page:
                                    cancel_page.runJavaScript("window.cancelLineCreation();")
                            
                            create_line_btn = current_player_tabs_dict.get('create_line_btn')
                            finish_line_btn = current_player_tabs_dict.get('finish_line_btn')
                            cancel_line_btn_local = current_player_tabs_dict.get('cancel_line_btn')
                            line_points_label = current_player_tabs_dict.get('line_points_label')
                            
                            if create_line_btn:
                                create_line_btn.setEnabled(True)
                            if finish_line_btn:
                                finish_line_btn.setEnabled(False)
                            if cancel_line_btn_local:
                                cancel_line_btn_local.setEnabled(False)
                            if line_points_label:
                                line_points_label.setText("Points: 0")
                
                cancel_line_btn.clicked.connect(cancel_line_creation)
                            
        def check_extend_line_request():
            # Get the current player's web_view
            if self.operations is None:
                return
            if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                return
            
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                return
            
            current_player_id = self.operations.get_player_id(current_player)
            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
            if not current_player_tabs_dict:
                return
            
            web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
            page = self._safe_get_webview_page(web_view)
            if not page:
                return
            
            def handle_result(result):
                if result and result != "null" and result != "undefined":
                    line_name = str(result).strip().strip('"').strip("'")
                    if line_name:
                        line_names = self.operations.get_player_line_names(current_player)
                        if line_names and line_name in line_names:
                            # Use Map tab sidebar flow: set combo, switch to Map tab, start extend
                            extend_line_combo = current_player_tabs_dict.get('extend_line_combo')
                            extend_line_btn = current_player_tabs_dict.get('extend_line_btn')
                            map_tab = current_player_tabs_dict.get('map_tab')
                            if extend_line_combo and extend_line_btn and map_tab:
                                idx = extend_line_combo.findText(line_name)
                                if idx >= 0:
                                    extend_line_combo.setCurrentIndex(idx)
                                    player_tabs = self.tabs.currentWidget()
                                    if player_tabs:
                                        map_tab_index = player_tabs.indexOf(map_tab)
                                        if map_tab_index >= 0:
                                            player_tabs.setCurrentIndex(map_tab_index)
                                            from PyQt6.QtCore import QTimer
                                            def trigger_extend():
                                                extend_line_btn.click()
                                            QTimer.singleShot(150, trigger_extend)
                            result_page = self._safe_get_webview_page(web_view)
                            if result_page:
                                result_page.runJavaScript("window.pendingExtendLine = null;")
            
            extend_result_page = self._safe_get_webview_page(web_view)
            if extend_result_page:
                extend_result_page.runJavaScript("window.pendingExtendLine || null;", handle_result)
        
        def check_add_station_request():
            if self.operations is None:
                return
            if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                return
            
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                return
            
            current_player_id = self.operations.get_player_id(current_player)
            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
            if not current_player_tabs_dict:
                return
            
            web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
            if not web_view or not web_view.page():
                return
            
            def handle_result(result):
                if not result or result == "null" or result == "undefined":
                    return
                try:
                    import json
                    data = json.loads(result) if isinstance(result, str) else result
                    if not data or not isinstance(data, dict):
                        return
                    line_name = data.get('lineName') or data.get('line_name')
                    lat = data.get('lat')
                    lon = data.get('lon')
                    if not line_name or lat is None or lon is None:
                        return
                    line_name = str(line_name).strip()
                    lat, lon = float(lat), float(lon)
                except (json.JSONDecodeError, ValueError, TypeError):
                    return
                try:
                    grid_x, grid_y = self.operations.find_nearest_point_on_line(line_name, lat, lon)
                except Exception as e:
                    if web_view and web_view.page():
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.pendingAddStation = null; window.pendingAddStationLat = null; window.pendingAddStationLon = null;")
                    QMessageBox.warning(self, "Error", f"Cannot add station: {e}")
                    return
                cost = self.operations.calculate_station_cost(1, grid_x, grid_y)
                reply = QMessageBox.question(
                    self,
                    "Add Station",
                    f"Build a station on line \"{line_name}\" at the clicked location?\n\nCost: ${cost}. The station will be created immediately.",
                    QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                    QMessageBox.StandardButton.Cancel
                )
                if reply != QMessageBox.StandardButton.Ok:
                    if web_view and web_view.page():
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.pendingAddStation = null; window.pendingAddStationLat = null; window.pendingAddStationLon = null;")
                    return
                station_name, ok = QInputDialog.getText(self, "Station Name", "Enter station name:", text=f"Station at ({int(grid_x)}, {int(grid_y)})")
                if not ok or not station_name.strip():
                    if web_view and web_view.page():
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.pendingAddStation = null; window.pendingAddStationLat = null; window.pendingAddStationLon = null;")
                    return
                station_name = station_name.strip()
                try:
                    result = self.operations.create_station_immediate(station_name, grid_x, grid_y, current_player, [line_name])
                    QMessageBox.information(self, "Station Created",
                        f"Station '{station_name}' has been built on line '{line_name}' at ({int(grid_x)}, {int(grid_y)}). Cost: ${result['cost']}.")
                    if web_view and web_view.page():
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.pendingAddStation = null; window.pendingAddStationLat = null; window.pendingAddStationLon = null;")
                    self.DisplayMapInfo()
                    self.refresh_construction_tab()
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to create station: {e}")
                    if web_view and web_view.page():
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.pendingAddStation = null; window.pendingAddStationLat = null; window.pendingAddStationLon = null;")
            
            def get_pending():
                return ("(window.pendingAddStation && window.pendingAddStationLat != null && window.pendingAddStationLon != null) ? "
                        "JSON.stringify({lineName: window.pendingAddStation, lat: window.pendingAddStationLat, lon: window.pendingAddStationLon}) : null;")
            
            station_pending_page = self._safe_get_webview_page(web_view)
            if station_pending_page:
                station_pending_page.runJavaScript(get_pending(), handle_result)
        
        def check_station_clicked():
            # Get the current player's web_view
            if self.operations is None:
                return
            if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                return
            
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                return
            
            current_player_id = self.operations.get_player_id(current_player)
            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
            if not current_player_tabs_dict:
                return
            
            web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
            page = self._safe_get_webview_page(web_view)
            if not page:
                return
            
            # Check for station line click data from JavaScript
            def handle_station_click(result):
                if not result or result == "null" or result == "undefined" or result is None:
                    return
                
                try:
                    import json
                    if isinstance(result, str):
                        station_data = json.loads(result)
                    else:
                        station_data = result
                    
                    if station_data and isinstance(station_data, dict) and 'x' in station_data and 'y' in station_data:
                        x = float(station_data['x'])
                        y = float(station_data['y'])
                        line_name = station_data.get('lineName', '')
                        # Clear the data in JavaScript
                        click_page = self._safe_get_webview_page(web_view)
                        if click_page:
                            click_page.runJavaScript("window.stationClickData = null;")
                        # Call on_line_clicked with the line data
                        lines = self.operations.get_lines_at_point(x, y)
                        if lines:
                            # Filter to only the matching line
                            matching_lines = [(line, name, owner) for line, name, owner in lines if name == line_name]
                            if matching_lines:
                                self.on_line_clicked(x, y, matching_lines)
                except Exception as e:
                    print(f"Error handling station click: {e}")
            
            # Poll for station click data
            station_click_page = self._safe_get_webview_page(web_view)
            if station_click_page:
                station_click_page.runJavaScript("JSON.stringify(window.stationClickData || null);", handle_station_click)
        
        def check_point_count():
            def handle_count(result):
                if result and result != "null" and result != "undefined":
                    count_str = str(result).strip()
                    if count_str and count_str.isdigit():
                        count = int(count_str)
                        if self.operations is None:
                            return
                        if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                            return
                        current_player = self.operations.get_current_player(self.current_player_index)
                        if current_player:
                            current_player_id = self.operations.get_player_id(current_player)
                            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
                            if current_player_tabs_dict:
                                point_label = current_player_tabs_dict.get('line_points_label')
                                if point_label:
                                    point_label.setText(f"Points: {count}")
            
            page = self._safe_get_webview_page(web_view)
            if page:
                page.runJavaScript("window.getCurrentPointCount ? window.getCurrentPointCount() : 0;", handle_count)
        
        page = self._safe_get_webview_page(web_view)
        if page:
            page.runJavaScript("""
            if (typeof window.pendingExtendLine === 'undefined') {
                window.pendingExtendLine = null;
            }
            if (typeof window.pendingPointCount === 'undefined') {
                window.pendingPointCount = 0;
            }
            if (typeof window.pendingExtendPointCount === 'undefined') {
                window.pendingExtendPointCount = 0;
            }
            if (typeof window.pendingAddStation === 'undefined') {
                window.pendingAddStation = null;
            }
            if (typeof window.pendingStationClicked === 'undefined') {
                window.pendingStationClicked = null;
            }
            if (typeof window.pendingCloseStation === 'undefined') {
                window.pendingCloseStation = null;
            }
            if (typeof window.getCurrentPointCount === 'undefined') {
                window.getCurrentPointCount = function() {
                    return window.pendingPointCount || 0;
                };
            }
            if (typeof window.getCurrentExtendPointCount === 'undefined') {
                window.getCurrentExtendPointCount = function() {
                    return window.pendingExtendPointCount || 0;
                };
            }
            if (!window.mapEventListenersSet) {
                window.addEventListener('mapLineExtend', function(e) {
                    window.pendingExtendLine = e.detail.lineName;
                });
                window.addEventListener('mapPointClicked', function(e) {
                    window.pendingPointCount = e.detail.count || 0;
                });
                window.addEventListener('mapExtendPointClicked', function(e) {
                    window.pendingExtendPointCount = e.detail.count || 0;
                });
                window.addEventListener('mapAddStation', function(e) {
                    window.pendingAddStation = e.detail.lineName;
                    window.pendingAddStationLat = e.detail.lat;
                    window.pendingAddStationLon = e.detail.lon;
                });
                window.addEventListener('mapStationClicked', function(e) {
                    if (e && e.detail) {
                        window.pendingStationClicked = {
                            lat: e.detail.lat || 0,
                            lon: e.detail.lon || 0
                        };
                    }
                });
                window.addEventListener('mapCloseStation', function(e) {
                    window.pendingCloseStation = e.detail.stationName;
                });
                window.mapEventListenersSet = true;
            }
        """)
        
        from PyQt6.QtCore import QTimer
        timer_key = f'extend_timer_{player_id}'
        if not hasattr(self, '_extend_line_timers'):
            self._extend_line_timers = {}
        if timer_key not in self._extend_line_timers:
            extend_check_timer = QTimer()
            extend_check_timer.timeout.connect(check_extend_line_request)
            extend_check_timer.start(500)
            self._extend_line_timers[timer_key] = extend_check_timer
        
        timer_key_point = f'point_timer_{player_id}'
        if not hasattr(self, '_point_count_timers'):
            self._point_count_timers = {}
        if timer_key_point not in self._point_count_timers:
            point_count_timer = QTimer()
            point_count_timer.timeout.connect(check_point_count)
            point_count_timer.start(200)
            self._point_count_timers[timer_key_point] = point_count_timer
        
        timer_key_add_station = f'add_station_timer_{player_id}'
        if not hasattr(self, '_add_station_timers'):
            self._add_station_timers = {}
        if timer_key_add_station not in self._add_station_timers:
            add_station_check_timer = QTimer()
            add_station_check_timer.timeout.connect(check_add_station_request)
            add_station_check_timer.start(500)
            self._add_station_timers[timer_key_add_station] = add_station_check_timer
        
        timer_key_station_click = f'station_click_timer_{player_id}'
        if not hasattr(self, '_station_click_timers'):
            self._station_click_timers = {}
        if timer_key_station_click not in self._station_click_timers:
            station_click_timer = QTimer()
            station_click_timer.timeout.connect(check_station_clicked)
            station_click_timer.start(200)
            self._station_click_timers[timer_key_station_click] = station_click_timer
        
        def check_edit_mode_click():
            if not getattr(self, 'edit_mode_enabled', False):
                return
            if self.operations is None:
                return
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                return
            current_player_id = self.operations.get_player_id(current_player)
            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
            if not current_player_tabs_dict:
                return
            web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
            page = self._safe_get_webview_page(web_view)
            if not page:
                return
            
            def handle_edit_click(result):
                if not result or result == "null" or result == "undefined" or result is None:
                    return
                try:
                    import json
                    if isinstance(result, str):
                        edit_data = json.loads(result)
                    else:
                        edit_data = result
                    
                    if edit_data and isinstance(edit_data, dict) and 'x' in edit_data and 'y' in edit_data:
                        x = int(edit_data['x'])
                        y = int(edit_data['y'])
                        # Clear the data in JavaScript immediately
                        edit_page = self._safe_get_webview_page(web_view)
                        if edit_page:
                            edit_page.runJavaScript("window.editModeClickData = null;")
                        self._toggle_population_point(x, y)
                        self.DisplayMapInfo()
                except Exception as e:
                    print(f"Error handling edit mode click: {e}")
            
            # Poll for edit mode click data
            edit_mode_page = self._safe_get_webview_page(web_view)
            if edit_mode_page:
                edit_mode_page.runJavaScript("JSON.stringify(window.editModeClickData || null);", handle_edit_click)
        
        timer_key_edit_mode = f'edit_mode_timer_{player_id}'
        if not hasattr(self, '_edit_mode_timers'):
            self._edit_mode_timers = {}
        if timer_key_edit_mode not in self._edit_mode_timers:
            edit_mode_timer = QTimer()
            edit_mode_timer.timeout.connect(check_edit_mode_click)
            edit_mode_timer.start(200)
            self._edit_mode_timers[timer_key_edit_mode] = edit_mode_timer
        
        def check_close_station():
            def handle_close(result):
                if not result or result == "null" or result == "undefined":
                    return
                if self.operations is None:
                    return
                if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                    return
                station_name = str(result).strip().strip('"').strip("'")
                if not station_name:
                    return
                current_player = self.operations.get_current_player(self.current_player_index)
                if not current_player:
                    return
                game_map = self.operations.get_map()
                if not hasattr(game_map, 'stations') or not game_map.stations:
                    return
                station_to_close = None
                for station in game_map.stations:
                    if hasattr(station, 'name') and station.name == station_name:
                        station_to_close = station
                        break
                if station_to_close:
                    all_lines = self.operations.get_map_lines()
                    for line_name, line in all_lines.items():
                        if hasattr(line, 'stations') and station_to_close in line.stations:
                            line.remove_station(station_to_close)
                    if station_to_close in game_map.stations:
                        game_map.stations.remove(station_to_close)
                    QMessageBox.information(self, "Station Closed", f"Station '{station_name}' has been closed.")
                    if web_view and web_view.page():
                        close_station_page = self._safe_get_webview_page(web_view)
                        if close_station_page:
                            close_station_page.runJavaScript("window.pendingCloseStation = null;")
                    self.DisplayMapInfo()
            
            close_station_check_page = self._safe_get_webview_page(web_view)
            if close_station_check_page:
                close_station_check_page.runJavaScript("window.pendingCloseStation || null;", handle_close)
        
        timer_key_close_station = f'close_station_timer_{player_id}'
        if not hasattr(self, '_close_station_timers'):
            self._close_station_timers = {}
        if timer_key_close_station not in self._close_station_timers:
            close_station_timer = QTimer()
            close_station_timer.timeout.connect(check_close_station)
            close_station_timer.start(500)
            self._close_station_timers[timer_key_close_station] = close_station_timer
        
        def check_extend_point_count():
            if self.operations is None:
                return
            if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                return
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                return
            current_player_id = self.operations.get_player_id(current_player)
            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
            if not current_player_tabs_dict:
                return
            
            extend_line_points_label = current_player_tabs_dict.get('extend_line_points_label')
            if not extend_line_points_label:
                return
            
            web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
            if not web_view:
                web_view = self.map_web_view
            
            def handle_count(count_str):
                if count_str and count_str != "null" and count_str != "undefined":
                    count_str = str(count_str).strip()
                    if count_str and count_str.isdigit():
                        count = int(count_str)
                        if extend_line_points_label:
                            extend_line_points_label.setText(f"Points: {count}")
            
            extend_page = self._safe_get_webview_page(web_view)
            if extend_page:
                extend_page.runJavaScript("window.getCurrentExtendPointCount ? window.getCurrentExtendPointCount() : 0;", handle_count)
        
        timer_key_extend_point = f'extend_point_timer_{player_id}'
        if not hasattr(self, '_extend_point_count_timers'):
            self._extend_point_count_timers = {}
        if timer_key_extend_point not in self._extend_point_count_timers:
            extend_point_count_timer = QTimer()
            extend_point_count_timer.timeout.connect(check_extend_point_count)
            extend_point_count_timer.start(200)
            self._extend_point_count_timers[timer_key_extend_point] = extend_point_count_timer
    
    def _generate_simple_map_html_no_webengine(self, game_map) -> str:
        """Generate static HTML map for QTextBrowser fallback when WebEngine is unavailable. No JavaScript."""
        import math
        center_lat = getattr(game_map, 'map_center_lat', None) or 40.7128
        center_lon = getattr(game_map, 'map_center_lon', None) or -74.0060
        zoom = 11

        def latlon_to_tile(lat, lon, z):
            n = 2 ** z
            x = int((lon + 180) / 360 * n) % n
            lat_rad = math.radians(max(-85, min(85, lat)))
            y = int((1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * n)
            return x, y

        cx, cy = latlon_to_tile(center_lat, center_lon, zoom)
        tiles = []
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                tx, ty = (cx + dx) % (2 ** zoom), cy + dy
                if 0 <= ty < (2 ** zoom):
                    url = f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
                    tiles.append(f'<img src="{url}" width="256" height="256" style="display:block;float:left;">')
        tiles_html = "".join(tiles)
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Map</title></head>
<body style="margin:0;padding:8px;font-family:sans-serif;background:#ddd;">
<div style="max-width:1280px;overflow:auto;">
{tiles_html}
</div>
<div style="clear:both;padding:8px;font-size:12px;color:#666;">
Static map (PyQt6-WebEngine not available). Run: poetry install && poetry run python main.py for full map.
</div>
</body></html>"""

    def _generate_osm_map_html_timeout_placeholder(self) -> str:
        """Return minimal HTML shown when map refresh exceeds time limit. User can click Refresh Map to retry."""
        return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Map</title></head>
<body style="margin:0;padding:20px;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;box-sizing:border-box;">
<p style="text-align:center;color:#666;">Map refresh timed out (15s limit).<br>Click <b>Refresh Map</b> to try again.</p>
</body></html>"""

    def _generate_osm_map_html(self, game_map, deadline: float = None) -> str:
        """Generate HTML for OSM map. If deadline (time.time() value) is set and exceeded, returns timeout placeholder."""
        import time
        import json
        import math
        start_time = time.time()
        if deadline is None:
            deadline = start_time + (MAP_REFRESH_MAX_MS / 1000.0)
        print(f"[DEBUG] _generate_osm_map_html called (deadline in {max(0, deadline - time.time()):.1f}s)")
        print(f"  - game_map: {game_map}")
        print(f"  - game_map type: {type(game_map)}")
        
        # Check if edit mode is enabled
        edit_mode_enabled = getattr(self, 'edit_mode_enabled', False)
            
            # Use map's center coordinates and presets
        from map_presets_nyc import (
            NYC_BOUNDS, NYC_ZOOM, is_water_location,
            is_nyc_map, get_nyc_default_center_lat, get_nyc_default_center_lon
        )
        from map_presets_ny_metro import (
            NY_METRO_BOUNDS, NY_METRO_ZOOM,
            is_ny_metro_map, get_ny_metro_default_center_lat, get_ny_metro_default_center_lon
        )
        from map_presets_standard import STANDARD_BOUNDS, STANDARD_ZOOM
        
        center_lat_check = game_map.map_center_lat if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None else None
        center_lon_check = game_map.map_center_lon if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None else None
        map_width_check = game_map.width if hasattr(game_map, 'width') else None
        map_height_check = game_map.height if hasattr(game_map, 'height') else None
        
        is_nyc_proper = hasattr(game_map, 'is_nyc_proper') and game_map.is_nyc_proper
        is_ny_metro = hasattr(game_map, 'is_ny_metro') and game_map.is_ny_metro
        
        # Debug output
        print(f"[DEBUG] Map type detection:")
        print(f"  - is_nyc_proper (from map): {is_nyc_proper}")
        print(f"  - is_ny_metro (from map): {is_ny_metro}")
        print(f"  - center_lat: {center_lat_check}, center_lon: {center_lon_check}")
        print(f"  - width: {map_width_check}, height: {map_height_check}")
        
        if not is_nyc_proper:
            is_nyc_proper = is_nyc_map(center_lat_check, center_lon_check, map_width_check, map_height_check)
            print(f"  - is_nyc_proper (after check): {is_nyc_proper}")
        
        if not is_ny_metro and not is_nyc_proper:
            is_ny_metro = is_ny_metro_map(center_lat_check, center_lon_check, map_width_check, map_height_check)
            print(f"  - is_ny_metro (after check): {is_ny_metro}")
        
        print(f"[DEBUG] Final map type: is_nyc_proper={is_nyc_proper}, is_ny_metro={is_ny_metro}")
        
        if is_nyc_proper:
            center_lat = center_lat_check if center_lat_check is not None else get_nyc_default_center_lat()
            center_lon = center_lon_check if center_lon_check is not None else get_nyc_default_center_lon()
        elif is_ny_metro:
            center_lat = center_lat_check if center_lat_check is not None else get_ny_metro_default_center_lat()
            center_lon = center_lon_check if center_lon_check is not None else get_ny_metro_default_center_lon()
        else:
            center_lat = game_map.map_center_lat if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None else STANDARD_BOUNDS['center_lat']
            center_lon = game_map.map_center_lon if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None else STANDARD_BOUNDS['center_lon']
            if center_lat is None:
                center_lat = STANDARD_BOUNDS['center_lat']
            if center_lon is None:
                center_lon = STANDARD_BOUNDS['center_lon']
        
        if is_nyc_proper:
            if 'min_lat' in NYC_BOUNDS and 'max_lat' in NYC_BOUNDS and 'min_lon' in NYC_BOUNDS and 'max_lon' in NYC_BOUNDS:
                min_lat = NYC_BOUNDS['min_lat']
                max_lat = NYC_BOUNDS['max_lat']
                min_lon = NYC_BOUNDS['min_lon']
                max_lon = NYC_BOUNDS['max_lon']
            else:
                min_lat = center_lat - 0.2
                max_lat = center_lat + 0.2
                min_lon = center_lon - 0.2
                max_lon = center_lon + 0.2
            if 'default_zoom' in NYC_ZOOM and 'min_zoom' in NYC_ZOOM and 'max_zoom' in NYC_ZOOM:
                zoom_level = NYC_ZOOM['default_zoom']
                min_zoom = NYC_ZOOM['min_zoom']
                max_zoom = NYC_ZOOM['max_zoom']
            else:
                zoom_level = 11
                min_zoom = 10
                max_zoom = 18
        elif is_ny_metro:
            if 'min_lat' in NY_METRO_BOUNDS and 'max_lat' in NY_METRO_BOUNDS and 'min_lon' in NY_METRO_BOUNDS and 'max_lon' in NY_METRO_BOUNDS:
                min_lat = NY_METRO_BOUNDS['min_lat']
                max_lat = NY_METRO_BOUNDS['max_lat']
                min_lon = NY_METRO_BOUNDS['min_lon']
                max_lon = NY_METRO_BOUNDS['max_lon']
                print(f"[DEBUG] NY Metro bounds: min_lat={min_lat}, max_lat={max_lat}, min_lon={min_lon}, max_lon={max_lon}")
            else:
                min_lat = center_lat - 0.3
                max_lat = center_lat + 0.3
                min_lon = center_lon - 0.3
                max_lon = center_lon + 0.3
                print(f"[DEBUG] NY Metro bounds (fallback): min_lat={min_lat}, max_lat={max_lat}, min_lon={min_lon}, max_lon={max_lon}")
            if 'default_zoom' in NY_METRO_ZOOM and 'min_zoom' in NY_METRO_ZOOM and 'max_zoom' in NY_METRO_ZOOM:
                zoom_level = NY_METRO_ZOOM['default_zoom']
                min_zoom = NY_METRO_ZOOM['min_zoom']
                max_zoom = NY_METRO_ZOOM['max_zoom']
            else:
                zoom_level = 10
                min_zoom = 8
                max_zoom = 15
        else:
            map_width = game_map.width if hasattr(game_map, 'width') and game_map.width and game_map.width > 0 else STANDARD_BOUNDS['default_width']
            map_height = game_map.height if hasattr(game_map, 'height') and game_map.height and game_map.height > 0 else STANDARD_BOUNDS['default_height']
            lat_span = (map_height / 2) / 111.0
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
            if lon_deg_per_km == 0:
                lon_deg_per_km = 111.0
            lon_span = (map_width / 2) / lon_deg_per_km
            min_lat = center_lat - lat_span
            max_lat = center_lat + lat_span
            min_lon = center_lon - lon_span
            max_lon = center_lon + lon_span
            if 'default_zoom' in STANDARD_ZOOM:
                zoom_level = STANDARD_ZOOM['default_zoom']
            else:
                zoom_level = 12
            map_diagonal = math.sqrt(map_width**2 + map_height**2)
            if map_diagonal > 0:
                min_zoom = max(1, int(12 - math.log10(map_diagonal / 50)))
            else:
                min_zoom = 8
            if 'max_zoom' in STANDARD_ZOOM:
                max_zoom = STANDARD_ZOOM['max_zoom']
            else:
                max_zoom = 19
        
        if center_lat is None or center_lon is None:
            if is_nyc_proper:
                center_lat = get_nyc_default_center_lat()
                center_lon = get_nyc_default_center_lon()
            elif is_ny_metro:
                center_lat = get_ny_metro_default_center_lat()
                center_lon = get_ny_metro_default_center_lon()
            else:
                center_lat = STANDARD_BOUNDS['center_lat']
                center_lon = STANDARD_BOUNDS['center_lon']
        
        if min_lat is None or max_lat is None or min_lon is None or max_lon is None:
            min_lat = center_lat - 0.2
            max_lat = center_lat + 0.2
            min_lon = center_lon - 0.2
            max_lon = center_lon + 0.2
        
        if zoom_level is None:
            zoom_level = 11
        if min_zoom is None:
            min_zoom = 10
        if max_zoom is None:
            max_zoom = 18
        
        # Get population data from UI (all game logic is in ui.py)
        population_data = []
        if self.operations is not None:
            population_data = self.operations.get_population_points_for_display(game_map)
            print(f"[DEBUG] Collected {len(population_data)} population points for display from ui.py")
        
        # Filter points to only those within the visible map bounds
        # OPTIMIZATION: Process events periodically during filtering to prevent UI freeze
        filtered_population_data = []
        for i, point in enumerate(population_data):
            lat = point.get('lat')
            lon = point.get('lon')
            if lat is not None and lon is not None:
                if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                    filtered_population_data.append(point)
            # Process events every 1000 points to prevent UI freeze
            if i > 0 and i % 1000 == 0:
                QApplication.processEvents()
        
        population_data = filtered_population_data
        print(f"[DEBUG] Filtered to {len(population_data)} population points within map bounds")
        
        # elapsed_time = time.time() - start_time
        
        lines_data = []
        map_width_val = game_map.width if hasattr(game_map, 'width') and game_map.width and game_map.width > 0 else 50
        map_height_val = game_map.height if hasattr(game_map, 'height') and game_map.height and game_map.height > 0 else 50
        grid_size = max(map_width_val, map_height_val)
        
        if is_nyc_proper:
            from map_presets_nyc import get_nyc_bounds
            nyc_bounds = get_nyc_bounds()
            nyc_lat_scale = (nyc_bounds['max_lat'] - nyc_bounds['min_lat']) / map_height_val if map_height_val > 0 else 0
            nyc_lon_scale = (nyc_bounds['max_lon'] - nyc_bounds['min_lon']) / map_width_val if map_width_val > 0 else 0
            nyc_min_lat = nyc_bounds['min_lat']
            nyc_min_lon = nyc_bounds['min_lon']
        else:
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
            if lon_deg_per_km == 0:
                lon_deg_per_km = 111.0
        
        if hasattr(game_map, 'lines') and game_map.lines and grid_size > 0:
            # OPTIMIZATION: Process events periodically during line processing to prevent UI freeze
            line_items = list(game_map.lines.items())
            for line_idx, (line_name, line) in enumerate(line_items):
                if hasattr(line, 'get_route'):
                    route = line.get_route()
                    if route:
                        line_points = []
                        for point in route:
                            # Handle both tuple/list coordinates and GridPoint objects
                            if isinstance(point, (list, tuple)) and len(point) >= 2:
                                x, y = float(point[0]), float(point[1])
                            elif hasattr(point, 'x') and hasattr(point, 'y'):
                                # GridPoint object
                                x, y = float(point.x), float(point.y)
                            else:
                                continue
                            
                            # Convert grid coordinates to lat/lon using pre-calculated constants
                            if is_nyc_proper:
                                lat = nyc_min_lat + y * nyc_lat_scale
                                lon = nyc_min_lon + x * nyc_lon_scale
                            else:
                                lat_km = (y - grid_size // 2)
                                lon_km = (x - grid_size // 2)
                                lat = center_lat + (lat_km / 111.0)
                                lon = center_lon + (lon_km / lon_deg_per_km)
                            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                                line_points.append([lat, lon])
                        if len(line_points) >= 2:
                            lines_data.append({'name': line_name, 'points': line_points, 'under_construction': False})
                # Process events every 10 lines to prevent UI freeze; respect 15s timeout
                if line_idx > 0 and line_idx % 10 == 0:
                    QApplication.processEvents()
                    if time.time() > deadline:
                        print(f"[WARNING] Map HTML generation aborted during lines (timeout)")
                        return self._generate_osm_map_html_timeout_placeholder()
        
        if grid_size > 0:
            build_queue = []
            if self.operations is not None:
                build_queue = self.operations.get_build_queue()
            for build_idx, build in enumerate(build_queue):
                if build.get('type') == 'line':
                    build_data = build.get('data', {})
                    line_name = build_data.get('name')
                    if not line_name:
                        continue
                    
                    points = build_data.get('points', [])
                    if not points:
                        continue
                    
                    line_points = []
                    for point in points:
                        # Handle both tuple/list coordinates and GridPoint objects
                        if isinstance(point, (list, tuple)) and len(point) >= 2:
                            x, y = float(point[0]), float(point[1])
                        elif hasattr(point, 'x') and hasattr(point, 'y'):
                            # GridPoint object
                            x, y = float(point.x), float(point.y)
                        else:
                            continue
                        
                        # Convert grid coordinates to lat/lon using pre-calculated constants
                        if is_nyc_proper:
                            lat = nyc_min_lat + y * nyc_lat_scale
                            lon = nyc_min_lon + x * nyc_lon_scale
                        else:
                            lat_km = (y - grid_size // 2)
                            lon_km = (x - grid_size // 2)
                            lat = center_lat + (lat_km / 111.0)
                            lon = center_lon + (lon_km / lon_deg_per_km)
                        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                            line_points.append([lat, lon])
                    
                    if len(line_points) >= 2:
                        existing_line_idx = None
                        for idx, existing_line in enumerate(lines_data):
                            if existing_line.get('name') == line_name:
                                existing_line_idx = idx
                                break
                        
                        if existing_line_idx is not None:
                            existing_line = lines_data[existing_line_idx]
                            existing_points = existing_line['points']
                            for point in line_points:
                                if point not in existing_points:
                                    existing_points.append(point)
                            existing_line['under_construction'] = True
                        else:
                            lines_data.append({'name': line_name, 'points': line_points, 'under_construction': True})
                # Process events every 5 builds to prevent UI freeze; respect 15s timeout
                if build_idx > 0 and build_idx % 5 == 0:
                    QApplication.processEvents()
                    if time.time() > deadline:
                        print(f"[WARNING] Map HTML generation aborted during build queue (timeout)")
                        return self._generate_osm_map_html_timeout_placeholder()
        
        if time.time() > deadline:
            print(f"[WARNING] Map HTML generation aborted before stations (timeout)")
            return self._generate_osm_map_html_timeout_placeholder()
        
        stations_data = []
        if hasattr(game_map, 'stations') and game_map.stations and grid_size > 0:
            stations_list = list(game_map.stations)
            for station_idx, station in enumerate(stations_list):
                if time.time() > deadline:
                    print(f"[WARNING] Map HTML generation aborted during stations (timeout)")
                    return self._generate_osm_map_html_timeout_placeholder()
                if hasattr(station, 'x') and hasattr(station, 'y'):
                    x, y = station.x, station.y
                    # Convert grid coordinates to lat/lon using pre-calculated constants
                    if is_nyc_proper:
                        lat = nyc_min_lat + y * nyc_lat_scale
                        lon = nyc_min_lon + x * nyc_lon_scale
                    else:
                        lat_km = (y - grid_size // 2)
                        lon_km = (x - grid_size // 2)
                        lat = center_lat + (lat_km / 111.0)
                        lon = center_lon + (lon_km / lon_deg_per_km)
                    if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                        station_name = station.name if hasattr(station, 'name') else str(station)
                        stations_data.append({'lat': lat, 'lon': lon, 'name': station_name})
                # Process events every 50 stations to prevent UI freeze
                if station_idx > 0 and station_idx % 50 == 0:
                    QApplication.processEvents()
        
        roads_data = []
        current_year = 1830 
        if self.operations is not None:
            try:
                current_year = self.operations.get_year()
            except:
                pass
        
        if hasattr(game_map, 'get_roads'):
            roads_dict = game_map.get_roads()
            if roads_dict and grid_size > 0:
                for road_idx, (road_name, road) in enumerate(roads_dict.items()):
                    # Check if road has year_built attribute
                    year_built = getattr(road, 'year_built', None)
                    if year_built is not None and year_built > current_year:
                        # Road hasn't been built yet - skip it
                        continue
                    
                    # Get road route
                    if not hasattr(road, 'get_route'):
                        continue
                    route = road.get_route()
                    if not route:
                        continue
                    
                    # Convert road route to lat/lon coordinates
                    road_points = []
                    for point in route:
                        # Handle both tuple/list coordinates and GridPoint objects
                        if isinstance(point, (list, tuple)) and len(point) >= 2:
                            x, y = float(point[0]), float(point[1])
                        elif hasattr(point, 'x') and hasattr(point, 'y'):
                            x, y = float(point.x), float(point.y)
                        else:
                            continue
                        # Convert grid coordinates to lat/lon
                        if is_nyc_proper:
                            lat = nyc_min_lat + y * nyc_lat_scale
                            lon = nyc_min_lon + x * nyc_lon_scale
                        else:
                            lat_km = (y - grid_size // 2)
                            lon_km = (x - grid_size // 2)
                            lat = center_lat + (lat_km / 111.0)
                            lon = center_lon + (lon_km / lon_deg_per_km)
                        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                            road_points.append([lat, lon])
                    
                    if len(road_points) >= 2:
                        roads_data.append({'name': road_name, 'points': road_points})
                # Process events every 20 roads to prevent UI freeze
                if road_idx > 0 and road_idx % 20 == 0:
                    QApplication.processEvents()
        
        # Historical roads rendering removed - no longer displaying grey lines on map
        # (NYC_HISTORICAL_ROADS processing code removed)
        
        # Ensure waterways display - OSM tiles automatically show waterways
        # No additional code needed as OSM tiles include water features
        

        # Performance timing
        elapsed_time = time.time() - start_time
        total_points = sum(len(road.get('points', [])) for road in roads_data) if roads_data else 0
        print(f"[DEBUG] Generated HTML in {elapsed_time:.2f}s: {len(roads_data)} roads ({total_points} points), {len(population_data)} population points, {len(lines_data)} lines")
        if elapsed_time > 5.0:
            print(f"[WARNING] Map HTML generation took {elapsed_time:.2f}s (target: < 5s)")
        
        # OPTIMIZATION: Process events after data collection but before HTML generation
        # This allows UI to update before the potentially slow HTML string building
        # Process events multiple times to ensure UI is responsive
        for _ in range(5):
            QApplication.processEvents()
        
        # OPTIMIZATION: JSON serialization can be slow for large datasets
        # Process events before and during serialization
        import json
        try:
            # Serialize data structures (can be slow for large datasets)
            population_json = json.dumps(population_data if population_data else [])
            QApplication.processEvents()
            lines_json = json.dumps(lines_data if lines_data else [])
            QApplication.processEvents()
            roads_json = json.dumps(roads_data if roads_data else [])
            QApplication.processEvents()
            stations_json = json.dumps(stations_data if stations_data else [])
            QApplication.processEvents()
        except Exception as e:
            print(f"[WARNING] JSON serialization failed: {e}, using empty arrays")
            population_json = "[]"
            lines_json = "[]"
            roads_json = "[]"
            stations_json = "[]"

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Transit Game Map</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        html, body {{ 
            margin: 0; 
            padding: 0; 
            width: 100%;
            height: 100%;
            overflow: hidden;
        }}
        #map {{ 
            height: 100vh; 
            width: 100vw; 
            position: absolute;
            top: 0;
            left: 0;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        // Wait for Leaflet to load before using it
        var mapInstance = null;
        var initAttempts = 0;
        var maxAttempts = 100; // 5 seconds max wait time
        
        function initMap() {{
            initAttempts++;
            if (typeof L === 'undefined') {{
                if (initAttempts < maxAttempts) {{
                    setTimeout(initMap, 50);
                }} else {{
                    console.error('Leaflet library failed to load after ' + maxAttempts + ' attempts');
                    document.getElementById('map').innerHTML = '<div style="padding: 20px; color: red;">Error: Failed to load Leaflet map library. Please check your internet connection.</div>';
                }}
                return;
            }}
            
            try {{
                mapInstance = L.map('map', {{
                    minZoom: {min_zoom},
                    maxZoom: {max_zoom}
                }}).setView([{center_lat}, {center_lon}], {zoom_level});
            
            var editModeEnabled = {json.dumps(edit_mode_enabled)};
            // Always use OpenStreetMap tiles
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                minZoom: {min_zoom},
                maxZoom: {max_zoom},
                tileSize: 256,
                zoomOffset: 0
            }}).addTo(mapInstance);
            
            // Add full grid lines if edit mode is enabled
            if (editModeEnabled) {{
                var editGridLayer = L.layerGroup();
                var mapWidth = {map_width_val};
                var mapHeight = {map_height_val};
                // Create grid lines for every grid point
                for (var x = 0; x <= mapWidth; x++) {{
                    var lon1 = {min_lon} + (x / mapWidth) * ({max_lon} - {min_lon});
                    L.polyline([[{min_lat}, lon1], [{max_lat}, lon1]], {{
                        color: '#000000',
                        weight: 1,
                        opacity: 0.5
                    }}).addTo(editGridLayer);
                }}
                for (var y = 0; y <= mapHeight; y++) {{
                    var lat1 = {min_lat} + (y / mapHeight) * ({max_lat} - {min_lat});
                    L.polyline([[lat1, {min_lon}], [lat1, {max_lon}]], {{
                        color: '#000000',
                        weight: 1,
                        opacity: 0.5
                    }}).addTo(editGridLayer);
                }}
                editGridLayer.addTo(mapInstance);
                window.editGridLayer = editGridLayer;
            }}
            
            mapInstance.setMaxBounds([[{min_lat}, {min_lon}], [{max_lat}, {max_lon}]]);
            
            // Population coloring: Green up to 1000, then yellow, orange, red
            function getPopulationColor(pop) {{
                if (pop === 0) {{
                    return '#cccccc'; // Light gray for 0 population
                }} else if (pop <= 1000) {{
                    return '#88ff00'; // Green for 0-1000
                }} else if (pop <= 2000) {{
                    return '#ffff00'; // Yellow for 1001-2000
                }} else if (pop <= 3000) {{
                    return '#ff8800'; // Orange for 2001-3000
                }} else if (pop <= 5000) {{
                    return '#ff4400'; // Red-orange for 3001-5000
                }} else {{
                    return '#ff0000'; // Red for 5000+
                }}
            }}
        
        var populationData = {population_json};
        // Store population markers for edit mode removal
        if (typeof window.populationMarkers === 'undefined') {{
            window.populationMarkers = [];
        }}
        if (populationData && populationData.length > 0) {{
        // Calculate max possible radius (expansion limit)
        // Max diameter is 1/8 of current (current max radius 20, so new max radius = 20/8 = 2.5)
        var maxPopPerLocation = 500000;  // Max population per location
        var maxRadius = Math.min(2.5, Math.sqrt(maxPopPerLocation / 50));
        
        populationData.forEach(function(item) {{
            var pop = item.pop || 0;
                var radius = Math.max(2, Math.min(2.5, Math.sqrt(Math.max(1, pop) / 50)));
                var color = getPopulationColor(pop);
            var marker = L.circleMarker([item.lat, item.lon], {{
                radius: radius,
                fillColor: color,
                color: '#000',
                weight: 1,
                opacity: 0.8,
                fillOpacity: 0.6
                }});
            
            // Add expansion limit indicator (dashed circle showing max possible size)
            if (radius < maxRadius) {{
                // Create a dashed circle using circleMarker with custom styling
                // Calculate approximate radius in meters for L.circle (1 pixel ≈ 0.264583 meters at zoom 11)
                var pixelToMeterRatio = 0.264583;  // Approximate conversion at zoom 11
                var radiusMeters = maxRadius * pixelToMeterRatio;
                
                var expansionLimit = L.circle([item.lat, item.lon], {{
                    radius: radiusMeters,
                    fill: false,
                    color: '#999',
                    weight: 1,
                    opacity: 0.6,
                    dashArray: '4, 4',
                    className: 'population-expansion-limit'
                }});
                expansionLimit.addTo(mapInstance);
            }}
            
            // In edit mode, clicking a population point removes it immediately
            if (editModeEnabled) {{
                marker.on('click', function(e) {{
                    e.originalEvent.stopPropagation(); // Prevent map click handler
                    // Convert lat/lon to grid coordinates
                    var mapWidth = {map_width_val};
                    var mapHeight = {map_height_val};
                    var gridX = Math.round(((e.latlng.lng - {min_lon}) / ({max_lon} - {min_lon})) * mapWidth);
                    var gridY = Math.round(((e.latlng.lat - {min_lat}) / ({max_lat} - {min_lat})) * mapHeight);
                    // Store edit click data for Python to pick up (removing this point)
                    window.editModeClickData = {{ x: gridX, y: gridY }};
                    // Immediately remove the marker from the map
                    mapInstance.removeLayer(marker);
                    // Remove from populationMarkers array if it exists
                    var index = window.populationMarkers.indexOf(marker);
                    if (index > -1) {{
                        window.populationMarkers.splice(index, 1);
                    }}
                }});
            }} else {{
                // Normal mode: show population popup with coordinates
                var gridX = item.grid_x !== undefined ? item.grid_x : 'N/A';
                var gridY = item.grid_y !== undefined ? item.grid_y : 'N/A';
                marker.bindPopup('Population: ' + pop + '<br/>Coordinates: (' + gridX + ', ' + gridY + ')');
            }}
            marker.addTo(mapInstance);
            // Store marker for potential removal
            window.populationMarkers.push(marker);
        }});
        }}
        
        var linesData = {lines_json};
            var lineLayers = [];
        linesData.forEach(function(line) {{
                var isUnderConstruction = line.under_construction || false;
                var lineColor = isUnderConstruction ? '#ff8800' : '#0066ff';
                var lineOpacity = isUnderConstruction ? 0.5 : 0.7;
                var lineWeight = isUnderConstruction ? 3 : 4;
                var lineOptions = {{
                color: lineColor,
                weight: lineWeight,
                opacity: lineOpacity
                }};
                if (isUnderConstruction) {{
                    lineOptions.dashArray = '10, 5';
                }}
                var lineLayer = L.polyline(line.points, lineOptions);
                
                var encodedName = encodeURIComponent(line.name || '');
                var popupContent = '<div><b>' + (line.name || '') + '</b><br/>' +
                    '<button class="extend-line-btn" data-line-name="' + encodedName + '">Extend Line</button><br/>' +
                    '<button class="add-station-btn" data-line-name="' + encodedName + '">Add Station</button></div>';
                lineLayer.bindPopup(popupContent);
                
                // Add click handler for station creation mode - intercept clicks before popup opens
                lineLayer.on('click', function(e) {{
                    // Store click location for Add Station (popup will use this)
                    window.lastLineClickLatLng = {{ lat: e.latlng.lat, lon: e.latlng.lng }};
                    window.lastLineClickName = line.name;
                    if (isCreatingStation && window.pendingStationLine && line.name === window.pendingStationLine) {{
                        // Convert lat/lon to grid coordinates
                        var lat = e.latlng.lat;
                        var lon = e.latlng.lng;
                        var mapWidth = {map_width_val};
                        var mapHeight = {map_height_val};
                        var minLat = {min_lat};
                        var maxLat = {max_lat};
                        var minLon = {min_lon};
                        var maxLon = {max_lon};
                        var gridX = Math.round(((lon - minLon) / (maxLon - minLon)) * mapWidth);
                        var gridY = Math.round(((lat - minLat) / (maxLat - minLat)) * mapHeight);
                        // Store coordinates and line name for Python to pick up
                        window.stationClickData = {{ x: gridX, y: gridY, lineName: line.name }};
                        // Trigger event that Python can listen to via polling
                        var event = new CustomEvent('stationLineClicked', {{
                            detail: {{ x: gridX, y: gridY, lineName: line.name }}
                        }});
                        window.dispatchEvent(event);
                        // Prevent popup from opening
                        if (e.originalEvent) {{
                            e.originalEvent.preventDefault();
                            e.originalEvent.stopPropagation();
                        }}
                        return false;
                    }}
                }});
                
                lineLayer.addTo(mapInstance);
                lineLayers.push(lineLayer);
            }});
        
        // Render roads (only roads that exist at current year)
        var roadsData = {json.dumps(roads_data if roads_data else [])};
        var roadLayers = [];
        roadsData.forEach(function(road) {{
            var roadOptions = {{
                color: '#888888',  // Gray color for roads
                weight: 2,
                opacity: 0.6
            }};
            var roadLayer = L.polyline(road.points, roadOptions);
            roadLayer.bindPopup('<div><b>' + (road.name || 'Road') + '</b></div>');
            roadLayer.addTo(mapInstance);
            roadLayers.push(roadLayer);
        }});
            
            var stationsData = {stations_json};
            stationsData.forEach(function(station) {{
                var stationMarker = L.marker([station.lat, station.lon], {{
                    icon: L.divIcon({{
                        className: 'station-marker',
                        html: '<div style="background-color: #00ff00; width: 12px; height: 12px; border-radius: 50%; border: 2px solid white; box-shadow: 0 0 4px rgba(0,0,0,0.5);"></div>',
                        iconSize: [12, 12]
                    }})
                }});
                var encodedName = encodeURIComponent(station.name || '');
                var stationPopupContent = '<div><b>Station: ' + (station.name || '') + '</b><br/>' +
                    '<button class="close-station-btn" data-station-name="' + encodedName + '">Close Station</button></div>';
                stationMarker.bindPopup(stationPopupContent);
                stationMarker.addTo(mapInstance);
            }});
            
            window.extendLine = function(lineName) {{
                var event = new CustomEvent('mapLineExtend', {{
                    detail: {{ lineName: lineName }}
                }});
                window.dispatchEvent(event);
            }};
            
            window.addStationToLine = function(lineName) {{
                var lat = null, lon = null;
                if (window.lastLineClickLatLng) {{
                    lat = window.lastLineClickLatLng.lat;
                    lon = window.lastLineClickLatLng.lon;
                }}
                var event = new CustomEvent('mapAddStation', {{
                    detail: {{ lineName: lineName, lat: lat, lon: lon }}
                }});
                window.dispatchEvent(event);
            }};
            
            mapInstance.on('popupopen', function(e) {{
                try {{
                    var popupEl = e.popup && e.popup.getElement ? e.popup.getElement() : null;
                    if (!popupEl) return;
                    var extendBtn = popupEl.querySelector('.extend-line-btn');
                    if (extendBtn) {{
                        extendBtn.addEventListener('click', function() {{
                            var name = extendBtn.getAttribute('data-line-name') || '';
                            window.extendLine(decodeURIComponent(name));
                        }});
                    }}
                    var addStationBtn = popupEl.querySelector('.add-station-btn');
                    if (addStationBtn) {{
                        addStationBtn.addEventListener('click', function() {{
                            var name = addStationBtn.getAttribute('data-line-name') || '';
                            window.addStationToLine(decodeURIComponent(name));
                        }});
                    }}
                    var closeStationBtn = popupEl.querySelector('.close-station-btn');
                    if (closeStationBtn) {{
                        closeStationBtn.addEventListener('click', function() {{
                            var name = closeStationBtn.getAttribute('data-station-name') || '';
                            window.closeStation(decodeURIComponent(name));
                        }});
                    }}
                }} catch (err) {{
                }}
            }});
            
            window.closeStation = function(stationName) {{
                var event = new CustomEvent('mapCloseStation', {{
                    detail: {{ stationName: stationName }}
                }});
                window.dispatchEvent(event);
            }};
            
            window.addEventListener('mapLineExtend', function(e) {{
                window.pendingExtendLine = e.detail.lineName;
            }});
            window.addEventListener('mapAddStation', function(e) {{
                window.pendingAddStation = e.detail.lineName;
                window.pendingAddStationLat = e.detail.lat;
                window.pendingAddStationLon = e.detail.lon;
            }});
            
            // Map click handler for creating lines/stations
            var clickedPoints = [];
            var isCreatingLine = false;
            var isCreatingStation = false;
            
            mapInstance.on('click', function(e) {{
                // Check if edit mode is enabled and handle population point editing
                if (editModeEnabled && !isCreatingLine && !isExtendingLine && !isCreatingStation) {{
                    // Convert lat/lon to grid coordinates
                    var mapWidth = {map_width_val};
                    var mapHeight = {map_height_val};
                    var gridX = Math.round(((e.latlng.lng - {min_lon}) / ({max_lon} - {min_lon})) * mapWidth);
                    var gridY = Math.round(((e.latlng.lat - {min_lat}) / ({max_lat} - {min_lat})) * mapHeight);
                    // Store edit click data for Python to pick up
                    window.editModeClickData = {{ x: gridX, y: gridY }};
                    return;
                }}
                
                if (isCreatingLine) {{
                    clickedPoints.push([e.latlng.lat, e.latlng.lng]);
                    var marker = L.marker(e.latlng, {{
                        icon: L.divIcon({{
                            className: 'line-point-marker',
                            html: '<div style="background-color: #0066ff; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                            iconSize: [10, 10]
                        }})
                    }}).addTo(mapInstance);
                    marker._linePointIndex = clickedPoints.length - 1;
                    marker._isLinePoint = true;
                    var event = new CustomEvent('mapPointClicked', {{
                        detail: {{ count: clickedPoints.length }}
                    }});
                    window.dispatchEvent(event);
                }} else if (isExtendingLine) {{
                    extendClickedPoints.push([e.latlng.lat, e.latlng.lng]);
                    var marker = L.marker(e.latlng, {{
                        icon: L.divIcon({{
                            className: 'extend-point-marker',
                            html: '<div style="background-color: #ff6600; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                            iconSize: [10, 10]
                        }})
                    }}).addTo(mapInstance);
                    marker._extendPointIndex = extendClickedPoints.length - 1;
                    marker._isExtendPoint = true;
                    // Update dotted preview line
                    updateExtendPreviewLine();
                    var event = new CustomEvent('mapExtendPointClicked', {{
                        detail: {{ count: extendClickedPoints.length }}
                    }});
                    window.dispatchEvent(event);
                }} else if (isCreatingStation) {{
                    var event = new CustomEvent('mapStationClicked', {{
                        detail: {{ lat: e.latlng.lat, lon: e.latlng.lng }}
                    }});
                    window.dispatchEvent(event);
                }}
            }});
            
            mapInstance.on('mousedown', function(e) {{
                if (e.originalEvent && e.originalEvent.button === 1) {{
                    if (isCreatingLine) {{
                        var clickedLat = e.latlng.lat;
                        var clickedLon = e.latlng.lng;
                        var minDist = Infinity;
                        var nearestIndex = -1;
                        for (var i = 0; i < clickedPoints.length; i++) {{
                            var point = clickedPoints[i];
                            var dist = Math.pow(point[0] - clickedLat, 2) + Math.pow(point[1] - clickedLon, 2);
                            if (dist < minDist) {{
                                minDist = dist;
                                nearestIndex = i;
                            }}
                        }}
                        if (nearestIndex >= 0 && minDist < 0.0001) {{
                            clickedPoints.splice(nearestIndex, 1);
                            mapInstance.eachLayer(function(layer) {{
                                if (layer instanceof L.Marker && layer._isLinePoint && layer._linePointIndex === nearestIndex) {{
                                    mapInstance.removeLayer(layer);
                                }} else if (layer instanceof L.Marker && layer._isLinePoint && layer._linePointIndex > nearestIndex) {{
                                    layer._linePointIndex--;
                                }}
                            }});
                            var event = new CustomEvent('mapPointClicked', {{
                                detail: {{ count: clickedPoints.length }}
                            }});
                            window.dispatchEvent(event);
                        }}
                    }} else if (isExtendingLine) {{
                        var clickedLat = e.latlng.lat;
                        var clickedLon = e.latlng.lng;
                        var minDist = Infinity;
                        var nearestIndex = -1;
                        for (var i = 0; i < extendClickedPoints.length; i++) {{
                            var point = extendClickedPoints[i];
                            var dist = Math.pow(point[0] - clickedLat, 2) + Math.pow(point[1] - clickedLon, 2);
                            if (dist < minDist) {{
                                minDist = dist;
                                nearestIndex = i;
                            }}
                        }}
                        if (nearestIndex >= 0 && minDist < 0.0001) {{
                            extendClickedPoints.splice(nearestIndex, 1);
                            mapInstance.eachLayer(function(layer) {{
                                if (layer instanceof L.Marker && layer._isExtendPoint && layer._extendPointIndex === nearestIndex) {{
                                    mapInstance.removeLayer(layer);
                                }} else if (layer instanceof L.Marker && layer._isExtendPoint && layer._extendPointIndex > nearestIndex) {{
                                    layer._extendPointIndex--;
                                }}
                            }});
                            // Update preview line after removing point
                            updateExtendPreviewLine();
                            var event = new CustomEvent('mapExtendPointClicked', {{
                                detail: {{ count: extendClickedPoints.length }}
                            }});
                            window.dispatchEvent(event);
                        }}
                    }}
                }}
            }});
            
            // Expose functions to Python via window object
            window.startLineCreation = function() {{
                isCreatingLine = true;
                clickedPoints = [];
            }};
            
            window.finishLineCreation = function() {{
                isCreatingLine = false;
                return clickedPoints;
            }};
            
            window.cancelLineCreation = function() {{
                isCreatingLine = false;
                clickedPoints = [];
                // Remove markers
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer.options.icon && 
                        layer.options.icon.options && 
                        layer.options.icon.options.className === 'line-point-marker') {{
                        mapInstance.removeLayer(layer);
                    }}
                }});
            }};
            
            window.isExtendingLine = false;
            window.extendingLineName = null;
            window.extendClickedPoints = [];
            window.extendPreviewLine = null;
            
            function updateExtendPreviewLine() {{
                // Remove existing preview line
                if (window.extendPreviewLine) {{
                    mapInstance.removeLayer(window.extendPreviewLine);
                    window.extendPreviewLine = null;
                }}
                
                // Find the line being extended
                if (!extendingLineName || extendClickedPoints.length === 0) {{
                    return;
                }}
                
                var lineToExtend = null;
                linesData.forEach(function(line) {{
                    if (line.name === extendingLineName) {{
                        lineToExtend = line;
                    }}
                }});
                
                if (!lineToExtend || !lineToExtend.points || lineToExtend.points.length === 0) {{
                    return;
                }}
                
                // Build preview points: last point of existing line + all extend points
                var previewPoints = [];
                // Start from last point of existing line
                var lastExistingPoint = lineToExtend.points[lineToExtend.points.length - 1];
                previewPoints.push([lastExistingPoint[0], lastExistingPoint[1]]);
                // Add all clicked extension points
                extendClickedPoints.forEach(function(point) {{
                    previewPoints.push([point[0], point[1]]);
                }});
                
                // Draw dotted preview line
                if (previewPoints.length > 1) {{
                    window.extendPreviewLine = L.polyline(previewPoints, {{
                        color: '#ff6600',
                        weight: 4,
                        opacity: 0.7,
                        dashArray: '10, 5'
                    }}).addTo(mapInstance);
                }}
            }};
            
            window.startLineExtension = function(lineName) {{
                isExtendingLine = true;
                extendingLineName = lineName;
                extendClickedPoints = [];
                // Initialize preview line
                updateExtendPreviewLine();
            }};
            
            window.finishLineExtension = function() {{
                isExtendingLine = false;
                var result = extendClickedPoints;
                extendClickedPoints = [];
                extendingLineName = null;
                // Remove preview line
                if (window.extendPreviewLine) {{
                    mapInstance.removeLayer(window.extendPreviewLine);
                    window.extendPreviewLine = null;
                }}
                return result;
            }};
            
            window.cancelLineExtension = function() {{
                isExtendingLine = false;
                extendClickedPoints = [];
                extendingLineName = null;
                // Remove preview line
                if (window.extendPreviewLine) {{
                    mapInstance.removeLayer(window.extendPreviewLine);
                    window.extendPreviewLine = null;
                }}
                // Remove markers
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer.options.icon && 
                        layer.options.icon.options && 
                        layer.options.icon.options.className === 'extend-point-marker') {{
                        mapInstance.removeLayer(layer);
                    }}
                }});
            }};
            
            window.pendingStationLine = null;
            window.pendingStationName = null;
            
            window.startStationCreation = function() {{
                isCreatingStation = true;
            }};
            
            window.startStationCreationWithLine = function(lineName) {{
                isCreatingStation = true;
                window.pendingStationLine = lineName;
                // Also set pendingAddStation for compatibility with existing handlers
                window.pendingAddStation = lineName;
            }};
            
            window.cancelStationCreation = function() {{
                isCreatingStation = false;
                window.pendingStationLine = null;
                window.pendingStationName = null;
            }};
            
            // Listen for line clicks during station creation
            window.addEventListener('mapLineClickedForStation', function(e) {{
                var lat = e.detail.lat;
                var lon = e.detail.lon;
                var lineName = e.detail.lineName;
                // Convert lat/lon to grid coordinates
                var mapWidth = {map_width_val};
                var mapHeight = {map_height_val};
                var minLat = {min_lat};
                var maxLat = {max_lat};
                var minLon = {min_lon};
                var maxLon = {max_lon};
                var gridX = Math.round(((lon - minLon) / (maxLon - minLon)) * mapWidth);
                var gridY = Math.round(((lat - minLat) / (maxLat - minLat)) * mapHeight);
                // Call Python function
                if (window.pywebview && window.pywebview.api) {{
                    window.pywebview.api.on_line_clicked_for_station(gridX, gridY, lineName);
                }} else {{
                    // Fallback: use custom event that Python can listen to
                    var event = new CustomEvent('pythonLineClickForStation', {{
                        detail: {{ x: gridX, y: gridY, lineName: lineName }}
                    }});
                    window.dispatchEvent(event);
                }}
            }});
            
            // Store map instance globally for Python access
            window.mapInstance = mapInstance;
            
                // Invalidate size after a short delay to ensure container is visible
                setTimeout(function() {{
                    if (mapInstance) {{
                        mapInstance.invalidateSize();
                    }}
                }}, 100);
            }} catch (error) {{
                console.error('Error initializing map:', error);
                document.getElementById('map').innerHTML = '<div style="padding: 20px; color: red;">Error initializing map: ' + error.message + '</div>';
            }}
        }}
        
        // Start initialization when page loads
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', initMap);
        }} else {{
            initMap();
        }}
        
        // Also invalidate size when window becomes visible (for tab switching)
        document.addEventListener('visibilitychange', function() {{
            if (mapInstance && !document.hidden) {{
                setTimeout(function() {{
                    mapInstance.invalidateSize();
                }}, 100);
            }}
        }});
    </script>
</body>
</html>"""
        
        QApplication.processEvents()
        return html
    
    def DisplayLinesControls(self, player=None):
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        lines_layout = self.player_tabs_dict[player_id]['lines_layout']
        if not lines_layout:
            return
        existing_widgets = []
        while lines_layout.count():
            child = lines_layout.takeAt(0)
            widget = child.widget()
            if widget:
                if isinstance(widget, QPushButton):
                    existing_widgets.append(widget)
                else:
                    widget.deleteLater()
        for widget in existing_widgets:
            lines_layout.addWidget(widget)
        if not any(isinstance(w, QPushButton) and w.text() == "Refresh Lines Controls" for w in existing_widgets):
            refresh_btn = QPushButton("Refresh Lines Controls")
            def refresh_with_player():
                self.DisplayLinesControls(player)
            refresh_btn.clicked.connect(refresh_with_player)
            lines_layout.addWidget(refresh_btn)
        scroll = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_widget.setLayout(scroll_layout)
        
        if not hasattr(self, 'fare_sliders'):
            self.fare_sliders = {}
        
        line_names = self.operations.get_player_line_names(player)
        build_queue = self.operations.get_build_queue()
        player_id = self.operations.get_player_id(player)
        
        construction_line_names = set()
        for build in build_queue:
            if build.get('type') == 'line' and build.get('player'):
                build_player_id = self.operations.get_player_id(build.get('player'))
                if build_player_id == player_id:
                    line_name = build.get('data', {}).get('name')
                    if line_name:
                        construction_line_names.add(line_name)
        
        all_line_names = set(line_names) if line_names else set()
        all_line_names.update(construction_line_names)
        
        if not all_line_names:
            scroll_layout.addWidget(QLabel("No lines owned."))
        else:
            for line_name in sorted(all_line_names):
                line = self.operations.get_map_lines().get(line_name)
                is_under_construction = line_name in construction_line_names and (not line or line_name not in (line_names if line_names else []))
                
                if is_under_construction:
                    line_group = QGroupBox(f"{line_name} (Under Construction)")
                    line_group.setStyleSheet("QGroupBox { font-weight: bold; color: orange; }")
                    line_layout = QVBoxLayout()
                    line_group.setLayout(line_layout)
                    
                    build_info = None
                    for build in build_queue:
                        if build.get('type') == 'line' and build.get('player'):
                            build_player_id = self.operations.get_player_id(build.get('player'))
                            if build_player_id == player_id:
                                build_line_name = build.get('data', {}).get('name')
                                if build_line_name == line_name:
                                    turns_remaining = build.get('turns_remaining')
                                    if turns_remaining is not None:
                                        line_layout.addWidget(QLabel(f"Turns remaining: {int(turns_remaining)}"))
                                    break
                    
                    scroll_layout.addWidget(line_group)
                    continue
                
                if not line:
                    continue
                
                line_layout = QHBoxLayout()
                line_layout.addWidget(QLabel(f"  {line_name}:"))
                
                fare_slider = QSlider(Qt.Orientation.Horizontal)
                fare_slider.setMinimum(1)
                fare_slider.setMaximum(15)
                fare_slider.setValue(self.operations.get_line_fare(line))
                fare_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
                fare_slider.setTickInterval(1)
                fare_slider.setMinimumWidth(200)
                
                fare_label = QLabel(f"${self.operations.get_line_fare(line)}")
                fare_label.setMinimumWidth(40)
                
                slider_key = (self.operations.get_player_id(player), line_name)
                self.fare_sliders[slider_key] = (fare_slider, fare_label, line)
                
                def make_fare_updater(slider, label, line_obj):
                    def update_fare(value):
                        self.operations.set_line_fare(line_obj, value)
                        label.setText(f"${value}")
                    return update_fare
                
                fare_slider.valueChanged.connect(make_fare_updater(fare_slider, fare_label, line))
                line_layout.addWidget(fare_slider)
                line_layout.addWidget(fare_label)
                
                # Schedule controls: Peak, Off Peak, Night frequencies
                schedule_group = QGroupBox("Schedule (trains/hour)")
                schedule_layout = QHBoxLayout()
                
                # Peak frequency
                peak_label = QLabel("Peak:")
                peak_spin = QDoubleSpinBox()
                peak_spin.setMinimum(0.0)
                peak_spin.setMaximum(60.0)
                peak_spin.setSingleStep(0.5)
                peak_spin.setValue(getattr(line, 'schedule', {}).get('peak', 4.0))
                schedule_layout.addWidget(peak_label)
                schedule_layout.addWidget(peak_spin)
                
                # Off Peak frequency
                off_peak_label = QLabel("Off Peak:")
                off_peak_spin = QDoubleSpinBox()
                off_peak_spin.setMinimum(0.0)
                off_peak_spin.setMaximum(60.0)
                off_peak_spin.setSingleStep(0.5)
                off_peak_spin.setValue(getattr(line, 'schedule', {}).get('off_peak', 2.0))
                schedule_layout.addWidget(off_peak_label)
                schedule_layout.addWidget(off_peak_spin)
                
                # Night frequency
                night_label = QLabel("Night:")
                night_spin = QDoubleSpinBox()
                night_spin.setMinimum(0.0)
                night_spin.setMaximum(60.0)
                night_spin.setSingleStep(0.5)
                night_spin.setValue(getattr(line, 'schedule', {}).get('night', 1.0))
                schedule_layout.addWidget(night_label)
                schedule_layout.addWidget(night_spin)
                
                # Max frequency display
                max_freq_label = QLabel(f"Max: {line.get_max_frequency_per_hour('peak'):.1f}/hr")
                schedule_layout.addWidget(max_freq_label)
                
                # Upgrade tracks button
                upgrade_tracks_btn = QPushButton("Add Track")
                upgrade_tracks_btn.setToolTip(f"Current tracks: {getattr(line, 'num_tracks', 1)}")
                def upgrade_tracks():
                    line.upgrade_tracks(1)
                    max_freq_label.setText(f"Max: {line.get_max_frequency_per_hour('peak'):.1f}/hr")
                    upgrade_tracks_btn.setToolTip(f"Current tracks: {getattr(line, 'num_tracks', 1)}")
                    self.DisplayLinesControls(player)
                upgrade_tracks_btn.clicked.connect(upgrade_tracks)
                schedule_layout.addWidget(upgrade_tracks_btn)
                
                schedule_group.setLayout(schedule_layout)
                
                # Update schedule when values change
                def update_schedule():
                    line.set_schedule_frequency('peak', peak_spin.value())
                    line.set_schedule_frequency('off_peak', off_peak_spin.value())
                    line.set_schedule_frequency('night', night_spin.value())
                    max_freq_label.setText(f"Max: {line.get_max_frequency_per_hour('peak'):.1f}/hr")
                
                peak_spin.valueChanged.connect(update_schedule)
                off_peak_spin.valueChanged.connect(update_schedule)
                night_spin.valueChanged.connect(update_schedule)
                
                frequency = self.operations.get_line_frequency(line)
                frequency_label = QLabel(f"Frequency: {frequency}/year")
                frequency_label.setMinimumWidth(120)
                line_layout.addWidget(frequency_label)
                edit_btn = QPushButton("Edit Line")
                def make_edit_handler(l, p):
                    return lambda: self.on_edit_line_clicked(l, p)
                edit_btn.clicked.connect(make_edit_handler(line, player))
                line_layout.addWidget(edit_btn)
                
                line_widget = QWidget()
                line_widget.setLayout(line_layout)
                scroll_layout.addWidget(line_widget)
                scroll_layout.addWidget(schedule_group)
        
        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        lines_layout.addWidget(scroll)
    
    def DisplayRollingStockInfo(self, player=None):
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        rolling_stock_trainsets_layout = self.player_tabs_dict[player_id]['rolling_stock_trainsets_layout']
        rolling_stock_locomotives_layout = self.player_tabs_dict[player_id]['rolling_stock_locomotives_layout']
        rolling_stock_railcars_layout = self.player_tabs_dict[player_id]['rolling_stock_railcars_layout']
        if not rolling_stock_trainsets_layout:
            return
        while rolling_stock_trainsets_layout.count():
            child = rolling_stock_trainsets_layout.takeAt(0)
            if child.widget():
                widget = child.widget()
                if isinstance(widget, QPushButton):
                    continue
                widget.deleteLater()
        while rolling_stock_locomotives_layout.count():
            child = rolling_stock_locomotives_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        while rolling_stock_railcars_layout.count():
            child = rolling_stock_railcars_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        refresh_btn = QPushButton("Refresh Rolling Stock")
        def refresh_with_player():
            self.DisplayRollingStockInfo(player)
        refresh_btn.clicked.connect(refresh_with_player)
        rolling_stock_trainsets_layout.addWidget(refresh_btn)
        
        trainsets_scroll = QScrollArea()
        trainsets_widget = QWidget()
        trainsets_layout = QVBoxLayout()
        trainsets_widget.setLayout(trainsets_layout)
        
        locomotives_scroll = QScrollArea()
        locomotives_widget = QWidget()
        locomotives_layout = QVBoxLayout()
        locomotives_widget.setLayout(locomotives_layout)
        
        railcars_scroll = QScrollArea()
        railcars_widget = QWidget()
        railcars_layout = QVBoxLayout()
        railcars_widget.setLayout(railcars_layout)
        
        trainsets_section = QVBoxLayout()
        trainsets = self.operations.get_player_trainsets(player)
        if trainsets:
            for i, ts in enumerate(trainsets):
                try:
                    loco_name = self.operations.get_trainset_locomotive_name(ts)
                    cars = len(self.operations.get_trainset_cars(ts))
                    cap = self.operations.get_trainset_capacity(ts)
                    line_assignment = self.operations.get_trainset_line_assignment(ts)
                    assignment_text = f" (Assigned to: {line_assignment})" if line_assignment else " (Unassigned)"
                    trainset_layout = QHBoxLayout()
                    trainset_label = QLabel(f"  Trainset {i+1}: {loco_name}, {cars} cars, Capacity: {cap}{assignment_text}")
                    trainset_layout.addWidget(trainset_label)
                    change_line_btn = QPushButton("Change Line")
                    def make_change_handler(t, p):
                        return lambda: self.on_change_trainset_line(t, p)
                    change_line_btn.clicked.connect(make_change_handler(ts, player))
                    trainset_layout.addWidget(change_line_btn)
                    sell_btn = QPushButton("Sell")
                    def make_sell_handler(t, p):
                        return lambda: self.on_sell_trainset_from_rolling_stock(t, p)
                    sell_btn.clicked.connect(make_sell_handler(ts, player))
                    trainset_layout.addWidget(sell_btn)
                    trainset_layout.addStretch()
                    trainset_widget = QWidget()
                    trainset_widget.setLayout(trainset_layout)
                    trainsets_section.addWidget(trainset_widget)
                except Exception:
                    trainsets_section.addWidget(QLabel(f"  Trainset {i+1}: [Error loading]"))
        else:
            trainsets_section.addWidget(QLabel("  No trainsets"))
        
        trainsets_w = QWidget()
        trainsets_w.setLayout(trainsets_section)
        trainsets_layout.addWidget(trainsets_w)
        
        locomotives_section = QVBoxLayout()
        locomotives = getattr(player, 'locomotives', [])
        if locomotives:
            for i, loco in enumerate(locomotives):
                loco_name = getattr(loco, 'name', f'Locomotive {i+1}')
                loco_layout = QHBoxLayout()
                loco_label = QLabel(f"  {loco_name}")
                loco_layout.addWidget(loco_label)
                sell_btn = QPushButton("Sell")
                def make_sell_handler(l, p):
                    return lambda: self.on_sell_locomotive_from_rolling_stock(l, p)
                sell_btn.clicked.connect(make_sell_handler(loco, player))
                loco_layout.addWidget(sell_btn)
                loco_layout.addStretch()
                loco_widget = QWidget()
                loco_widget.setLayout(loco_layout)
                locomotives_section.addWidget(loco_widget)
        else:
            locomotives_section.addWidget(QLabel("  No locomotives"))
        
        locomotives_w = QWidget()
        locomotives_w.setLayout(locomotives_section)
        locomotives_layout.addWidget(locomotives_w)
        
        railcars_section = QVBoxLayout()
        railcars = getattr(player, 'railcars', [])
        if railcars:
            for i, car in enumerate(railcars):
                car_name = getattr(car, 'name', f'Railcar {i+1}')
                cap = getattr(car, 'capacity', 0)
                railcar_layout = QHBoxLayout()
                railcar_label = QLabel(f"  {car_name} (Capacity: {cap})")
                railcar_layout.addWidget(railcar_label)
                sell_btn = QPushButton("Sell")
                def make_sell_handler(c, p):
                    return lambda: self.on_sell_railcar_from_rolling_stock(c, p)
                sell_btn.clicked.connect(make_sell_handler(car, player))
                railcar_layout.addWidget(sell_btn)
                railcar_layout.addStretch()
                railcar_widget = QWidget()
                railcar_widget.setLayout(railcar_layout)
                railcars_section.addWidget(railcar_widget)
        else:
            railcars_section.addWidget(QLabel("  No railcars"))
        
        railcars_w = QWidget()
        railcars_w.setLayout(railcars_section)
        railcars_layout.addWidget(railcars_w)
        
        trainsets_scroll.setWidget(trainsets_widget)
        trainsets_scroll.setWidgetResizable(True)
        rolling_stock_trainsets_layout.addWidget(trainsets_scroll)
        
        locomotives_scroll.setWidget(locomotives_widget)
        locomotives_scroll.setWidgetResizable(True)
        rolling_stock_locomotives_layout.addWidget(locomotives_scroll)
        
        railcars_scroll.setWidget(railcars_widget)
        railcars_scroll.setWidgetResizable(True)
        rolling_stock_railcars_layout.addWidget(railcars_scroll)
    
    def DisplayWorkersControls(self, player=None):
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        workers_layout = self.player_tabs_dict[player_id]['workers_layout']
        if not workers_layout:
            return
        while workers_layout.count():
            child = workers_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        refresh_btn = QPushButton("Refresh Workers Controls")
        def refresh_with_player():
            self.DisplayWorkersControls(player)
        refresh_btn.clicked.connect(refresh_with_player)
        workers_layout.addWidget(refresh_btn)
        
        scroll = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_widget.setLayout(scroll_layout)
        
        from worker import ENGINEER, CONSTRUCTION_WORKER, LAWYER, MAINTENANCE_WORKER
        
        worker_section = QVBoxLayout()
        worker_label = QLabel("Worker Salaries:")
        worker_label.setStyleSheet("font-weight: bold;")
        worker_section.addWidget(worker_label)
        
        def create_worker_salary_control(worker_type, rental, p):
            if not rental:
                return None
            
            worker_count = rental.quantity
            strike_info = ""
            if rental.is_on_strike():
                strike_info = f" [ON STRIKE - {rental.strike_turns_remaining} turns remaining! Demanding {rental.demanded_multiplier:.2f}x]"
                strike_info = f"<span style='color: red; font-weight: bold;'>{strike_info}</span>"
            
            worker_layout = QHBoxLayout()
            worker_label_text = f"  {worker_type.name} Salary Multiplier ({worker_count} {worker_type.name.lower()}s):{strike_info}"
            worker_label_widget = QLabel(worker_label_text)
            worker_label_widget.setTextFormat(Qt.TextFormat.RichText)
            worker_layout.addWidget(worker_label_widget)
            
            current_multiplier = worker_type.get_salary_multiplier()
            
            salary_slider = QSlider(Qt.Orientation.Horizontal)
            salary_slider.setMinimum(5)
            salary_slider.setMaximum(50)
            salary_slider.setValue(int(current_multiplier * 10))
            salary_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            salary_slider.setTickInterval(5)
            salary_slider.setMinimumWidth(200)
            
            salary_label = QLabel(f"{current_multiplier:.1f}x")
            salary_label.setMinimumWidth(50)
            
            current_year = self.operations.get_year()
            yearly_cost = rental.get_yearly_cost(current_year)
            cost_label = QLabel(f"Yearly cost: ${yearly_cost}")
            
            salary_key = (self.operations.get_player_id(p), worker_type.name)
            if not hasattr(self, 'salary_sliders'):
                self.salary_sliders = {}
            self.salary_sliders[salary_key] = (salary_slider, salary_label, worker_type)
            
            def make_salary_updater(slider, label, cost_label, worker_type_obj, rental_obj, current_year):
                def update_salary(value):
                    multiplier = value / 10.0
                    worker_type_obj.set_salary_multiplier(multiplier)
                    label.setText(f"{multiplier:.1f}x")
                    if rental_obj:
                        new_cost = rental_obj.get_yearly_cost(current_year)
                        cost_label.setText(f"Yearly cost: ${new_cost}")
                return update_salary
            
            salary_slider.valueChanged.connect(make_salary_updater(salary_slider, salary_label, cost_label, worker_type, rental, current_year))
            
            worker_layout.addWidget(salary_slider)
            worker_layout.addWidget(salary_label)
            worker_layout.addWidget(cost_label)
            
            fire_btn = QPushButton(f"Fire {worker_type.name}s")
            def make_fire_handler(p, wt):
                return lambda: self.on_fire_workers_clicked(p, wt)
            fire_btn.clicked.connect(make_fire_handler(p, worker_type))
            worker_layout.addWidget(fire_btn)
            
            worker_widget = QWidget()
            worker_widget.setLayout(worker_layout)
            return worker_widget
        
        engineer_rental = self.operations.get_player_worker_rental(player, ENGINEER.name)
        if engineer_rental:
            engineer_widget = create_worker_salary_control(ENGINEER, engineer_rental, player)
            if engineer_widget:
                worker_section.addWidget(engineer_widget)
        
        construction_rental = self.operations.get_player_worker_rental(player, CONSTRUCTION_WORKER.name)
        if construction_rental:
            construction_widget = create_worker_salary_control(CONSTRUCTION_WORKER, construction_rental, player)
            if construction_widget:
                worker_section.addWidget(construction_widget)
        
        lawyer_rental = self.operations.get_player_worker_rental(player, LAWYER.name)
        if lawyer_rental:
            lawyer_widget = create_worker_salary_control(LAWYER, lawyer_rental, player)
            if lawyer_widget:
                worker_section.addWidget(lawyer_widget)
        
        maintenance_rental = self.operations.get_player_worker_rental(player, MAINTENANCE_WORKER.name)
        if maintenance_rental:
            maintenance_widget = create_worker_salary_control(MAINTENANCE_WORKER, maintenance_rental, player)
            if maintenance_widget:
                worker_section.addWidget(maintenance_widget)
        
        if not (engineer_rental or construction_rental or lawyer_rental or maintenance_rental):
            worker_section.addWidget(QLabel("  No workers rented."))
        
        worker_widget = QWidget()
        worker_widget.setLayout(worker_section)
        scroll_layout.addWidget(worker_widget)
        
        hire_section = QVBoxLayout()
        hire_label = QLabel("Hire Workers (from Marketplace):")
        hire_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        hire_section.addWidget(hire_label)
        hire_section.addWidget(QLabel("Go to Marketplace tab to rent workers."))
        hire_widget = QWidget()
        hire_widget.setLayout(hire_section)
        scroll_layout.addWidget(hire_widget)
        
        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        workers_layout.addWidget(scroll)
    
    def DisplayLawInfo(self, player=None):
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        law_layout = self.player_tabs_dict[player_id]['law_layout']
        if not law_layout:
            return
        if self.operations is None:
            law_layout.addWidget(QLabel("Game has not started yet."))
            return
        while law_layout.count():
            child = law_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        refresh_btn = QPushButton("Refresh Law Info")
        def refresh_with_player():
            self.DisplayLawInfo(player)
        refresh_btn.clicked.connect(refresh_with_player)
        law_layout.addWidget(refresh_btn)
        
        scroll = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_widget.setLayout(scroll_layout)
        
        try:
            from lawsuit import LAWSUIT_EXCUSES
            from worker import LAWYER
            
            try:
                player_section = QVBoxLayout()
                
                lawyer_count = self.operations.get_player_lawyer_count(player)
                available = self.operations.get_player_available_lawyer_count(player)
                lawyer_label = QLabel(f"  Lawyers: {lawyer_count} total, {available} available for lawsuits (rest drafting laws)")
                player_section.addWidget(lawyer_label)
                
                excuses = self.operations.get_player_lawsuit_excuses(player)
                if excuses:
                    excuses_label = QLabel(f"  Lawsuit Excuses: {len(excuses)}")
                    player_section.addWidget(excuses_label)
                    for excuse in excuses:
                        try:
                            current_year = self.operations.get_year()
                            damages = excuse.get_damages(current_year) if hasattr(excuse, 'get_damages') else excuse.base_damages
                            excuse_label = QLabel(f"    - {excuse.cause}: ${damages} (${excuse.base_damages} base)")
                            player_section.addWidget(excuse_label)
                        except Exception as e:
                            excuse_label = QLabel(f"    - {excuse.cause if hasattr(excuse, 'cause') else 'Unknown'}: Error displaying damages")
                            player_section.addWidget(excuse_label)
                
                draft_options = self.operations.get_law_draft_options(player)
                in_progress, ready_list = self.operations.get_player_drafts(player)
                player_section.addWidget(QLabel("--- Law drafting (lawyers draft; occupied lawyers cannot fight lawsuits) ---"))
                if in_progress:
                    player_section.addWidget(QLabel(f"  Drafting: {len(in_progress)} law(s) in progress (ready next year)"))
                if ready_list:
                    for law_type, _ in ready_list:
                        lbl = next((o['label'] for o in draft_options if o['id'] == law_type), law_type)
                        row = QHBoxLayout()
                        row.addWidget(QLabel(f"  Ready: {lbl}"))
                        sub_btn = QPushButton("Submit to Committee")
                        opts = {o['id']: o for o in draft_options}
                        req_target = opts.get(law_type, {}).get('requires_target', False)
                        target_combo = QComboBox()
                        for p in self.operations.get_active_players():
                            if p != player:
                                target_combo.addItem(self.operations.get_player_name(p), p)
                        def make_submit(p, lt, need_tgt, tgt_combo):
                            def submit():
                                target = tgt_combo.currentData() if need_tgt else None
                                r = self.operations.submit_law_draft(p, lt, target)
                                QMessageBox.information(self, "Law Submission", r['message'])
                                self.DisplayLawInfo(p)
                            return submit
                        sub_btn.clicked.connect(make_submit(player, law_type, req_target, target_combo))
                        row.addWidget(sub_btn)
                        if req_target:
                            row.addWidget(QLabel("Target:"))
                            row.addWidget(target_combo)
                        row.addStretch()
                        w = QWidget()
                        w.setLayout(row)
                        player_section.addWidget(w)
                draft_row = QHBoxLayout()
                draft_combo = QComboBox()
                for o in draft_options:
                    draft_combo.addItem(o['label'], o['id'])
                start_btn = QPushButton("Start Draft (1 lawyer, 1 year)")
                def on_start_draft():
                    lt = draft_combo.currentData()
                    r = self.operations.start_law_draft(player, lt)
                    QMessageBox.information(self, "Law Draft", r['message'])
                    self.DisplayLawInfo(player)
                start_btn.clicked.connect(on_start_draft)
                draft_row.addWidget(QLabel("Draft law:"))
                draft_row.addWidget(draft_combo)
                draft_row.addWidget(start_btn)
                draft_row.addStretch()
                dw = QWidget()
                dw.setLayout(draft_row)
                player_section.addWidget(dw)
                
                player_section.addWidget(QLabel("--- File lawsuit ---"))
                file_btn = QPushButton(f"File Lawsuit ({self.operations.get_player_name(player)})")
                def make_file_handler(p):
                    return lambda: self.on_file_lawsuit_clicked(p)
                file_btn.clicked.connect(make_file_handler(player))
                player_section.addWidget(file_btn)
                
                player_widget = QWidget()
                player_widget.setLayout(player_section)
                scroll_layout.addWidget(player_widget)
            except Exception as e:
                error_label = QLabel(f"Error displaying player {self.operations.get_player_name(player)}: {str(e)}")
                scroll_layout.addWidget(error_label)
        except Exception as e:
            scroll_layout.addWidget(QLabel(f"Error loading lawsuit info: {str(e)}"))
        
        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        law_layout.addWidget(scroll)
    
    def on_file_lawsuit_clicked(self, plaintiff):
        
        from lawsuit import LAWSUIT_EXCUSES, Lawsuit
        from worker import LAWYER
        
        excuses = self.operations.get_player_lawsuit_excuses(plaintiff)
        current_year = self.operations.get_year()
        
        
        dlg = QDialog(self)
        dlg.setWindowTitle("File Lawsuit")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Select excuse (or make one up):"))
        excuse_combo = QComboBox()
        excuse_combo.addItem("Make up a cause (10% base chance)", None)
        for excuse in excuses:
            damages = excuse.get_damages(current_year)
            excuse_combo.addItem(f"{excuse.cause} (${damages})", excuse)
        layout.addWidget(excuse_combo)
        
        
        made_up_cause_input = QLineEdit()
        made_up_cause_input.setPlaceholderText("Enter your made-up cause (e.g., 'Unfair Competition')")
        made_up_cause_input.setEnabled(False)  
        layout.addWidget(QLabel("Made-up cause:"))
        layout.addWidget(made_up_cause_input)
        
        def on_excuse_changed(index):
            selected_excuse = excuse_combo.itemData(index)
            
            made_up_cause_input.setEnabled(index == 0)
            if index != 0:
                made_up_cause_input.clear()
        
        excuse_combo.currentIndexChanged.connect(on_excuse_changed)
        
        on_excuse_changed(0)  
        
        layout.addWidget(QLabel("Select defendant:"))
        defendant_combo = QComboBox()
        active_players = self.operations.get_active_players()
        for player in active_players:
            if player != plaintiff:
                defendant_combo.addItem(self.operations.get_player_name(player), player)
        layout.addWidget(defendant_combo)
        
        if defendant_combo.count() == 0:
            QMessageBox.information(self, "No Defendants", "No other players available to sue.")
            dlg.close()
            return
        
        btn_layout = QHBoxLayout()
        file_btn = QPushButton("File Lawsuit")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(file_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dlg.setLayout(layout)
        
        def do_file():
            selected_excuse = excuse_combo.currentData()
            defendant = defendant_combo.currentData()
            
            is_made_up = excuse_combo.currentIndex() == 0
            made_up_cause = made_up_cause_input.text().strip() if is_made_up else ""
            
            if not defendant:
                QMessageBox.warning(dlg, "Error", "Please select a defendant.")
                return
            
            
            if is_made_up and not made_up_cause:
                QMessageBox.warning(dlg, "Error", "Please enter a made-up cause or select an excuse.")
                return
            
            
            plaintiff_lawyers = self.operations.get_player_available_lawyer_count(plaintiff)
            defendant_lawyers = self.operations.get_player_available_lawyer_count(defendant)
            
            
            lawsuit = Lawsuit(plaintiff, defendant, selected_excuse, current_year, made_up_cause)
            winner = lawsuit.resolve(plaintiff_lawyers, defendant_lawyers)
            
            
            if selected_excuse:
                self.operations.remove_lawsuit_excuse_from_player(plaintiff, selected_excuse)
            
            
            cause_text = lawsuit.cause
            court_costs = int(lawsuit.damages * 0.1)
            if winner == plaintiff:
                result_msg = f"Lawsuit won by {self.operations.get_player_name(plaintiff)}!\n"
                result_msg += f"Cause: {cause_text}\n"
                result_msg += f"{self.operations.get_player_name(defendant)} must pay ${lawsuit.damages} in damages.\n"
                result_msg += f"Both sides paid ${court_costs} in court costs.\n"
                result_msg += f"Lawyers: {self.operations.get_player_name(plaintiff)} ({plaintiff_lawyers}) vs {self.operations.get_player_name(defendant)} ({defendant_lawyers})"
            else:
                reparations = int(lawsuit.damages * 0.5)
                result_msg = f"Lawsuit won by {self.operations.get_player_name(defendant)}!\n"
                result_msg += f"Cause: {cause_text}\n"
                result_msg += f"{self.operations.get_player_name(plaintiff)} must pay ${reparations} in reparations to {self.operations.get_player_name(defendant)}.\n"
                result_msg += f"Both sides paid ${court_costs} in court costs.\n"
                result_msg += f"Lawyers: {self.operations.get_player_name(plaintiff)} ({plaintiff_lawyers}) vs {self.operations.get_player_name(defendant)} ({defendant_lawyers})"
            
            
            QMessageBox.warning(
                self, 
                f"You've been sued by {self.operations.get_player_name(plaintiff)}!",
                f"{self.operations.get_player_name(defendant)}, you have been sued by {self.operations.get_player_name(plaintiff)}!\n\n"
                f"Cause: {cause_text}\n"
                f"Damages sought: ${lawsuit.damages}\n\n"
                f"Your lawyers: {defendant_lawyers}\n"
                f"{self.operations.get_player_name(plaintiff)}'s lawyers: {plaintiff_lawyers}"
            )
            
            QMessageBox.information(self, "Lawsuit Result", result_msg)
            dlg.close()
            
            
            self.DisplayLawInfo(plaintiff)
            self.DisplayMarketplaceInfo()
            self.DisplayLinesControls(plaintiff)
            self.DisplayWorkersControls(plaintiff)
        
        file_btn.clicked.connect(do_file)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_lobby_clicked(self, player):
        """Open lobbying dialog: choose option and amount; all game logic in operations.try_lobby."""
        options = self.operations.get_lobby_options(player)
        if not options:
            QMessageBox.information(self, "Lobby", "No lobby options available.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Lobby Government")
        dlg.setGeometry(200, 200, 500, 280)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Pay money to influence the government. The more you pay, the more likely it is to succeed."))

        layout.addWidget(QLabel("What do you want to lobby for?"))
        option_combo = QComboBox()
        for o in options:
            option_combo.addItem(o['label'], o)
        layout.addWidget(option_combo)

        target_combo = QComboBox()
        target_label = QLabel("Target player:")
        layout.addWidget(target_label)
        layout.addWidget(target_combo)
        others = [p for p in self.operations.get_active_players() if p != player]
        for p in others:
            target_combo.addItem(self.operations.get_player_name(p), p)
        def toggle_target(ix):
            o = option_combo.itemData(ix)
            need = o and o.get('requires_target')
            target_label.setVisible(need)
            target_combo.setVisible(need)
        option_combo.currentIndexChanged.connect(toggle_target)
        toggle_target(0)

        amount_layout = QHBoxLayout()
        amount_layout.addWidget(QLabel("Amount ($):"))
        amount_spin = QSpinBox()
        amount_spin.setRange(1000, 500000)
        amount_spin.setSingleStep(5000)
        amount_spin.setValue(25000)
        amount_spin.setPrefix("$ ")
        amount_layout.addWidget(amount_spin)
        layout.addLayout(amount_layout)

        chance_label = QLabel()
        def update_chance():
            amt = amount_spin.value()
            chance = self.operations.get_lobby_success_chance(amt)
            chance_label.setText(f"Estimated success chance: {chance * 100:.0f}%")
        amount_spin.valueChanged.connect(update_chance)
        update_chance()
        layout.addWidget(chance_label)

        money = self.operations.get_player_money(player)
        layout.addWidget(QLabel(f"Your money: ${money}"))

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Lobby")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)

        def do_lobby():
            amt = amount_spin.value()
            o = option_combo.currentData()
            option_id = o['id'] if o else 'drop_excuse'
            target = target_combo.currentData() if target_combo.isVisible() else None
            result = self.operations.try_lobby(player, amt, option_id=option_id, target_player=target)
            dlg.close()
            title = "Lobbying Succeeded" if result['success'] else "Lobbying Failed"
            QMessageBox.information(self, title, result['message'])
            self.DisplayLawsuitInfo(player)

        ok_btn.clicked.connect(do_lobby)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def refresh_construction_tab(self):
        
        if not self.operations:
            return
        
        if not hasattr(self, 'player_tabs_dict'):
            return
        
        build_queue = self.operations.get_build_queue()
        players = self.operations.get_players()
        
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            in_progress_layout = player_tabs_dict.get('in_progress_layout')
            complete_jobs_layout = player_tabs_dict.get('complete_jobs_layout')
            
            if in_progress_layout is None or complete_jobs_layout is None:
                continue
            
            # Clear existing widgets
            while in_progress_layout.count():
                child = in_progress_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            
            while complete_jobs_layout.count():
                child = complete_jobs_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            
            player = next((p for p in players if self.operations.get_player_id(p) == player_id), None)
            if not player:
                continue
            
            player_builds = [idx for idx, build in enumerate(build_queue) 
                           if build.get('player') and self.operations.get_player_id(build.get('player')) == player_id]
            
            if not player_builds:
                no_jobs_label = QLabel("No construction jobs in queue")
                in_progress_layout.addWidget(no_jobs_label)
                in_progress_layout.addStretch()
                complete_jobs_layout.addStretch()
                continue
            
            in_progress_label = QLabel("In-Progress Construction Jobs:")
            in_progress_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
            in_progress_layout.addWidget(in_progress_label)
            
            for idx in player_builds:
                build = build_queue[idx]
                build_type = build['type']
                
                turns_remaining = build.get('turns_remaining', None)
                if turns_remaining is not None:
                    turns_remaining = int(turns_remaining)
                build_player = build.get('player')
                if not build_player:
                    continue
                player_name = self.operations.get_player_name(build_player)
                
                # Create job widget
                job_widget = QWidget()
                job_layout = QVBoxLayout()
                job_widget.setLayout(job_layout)
                
                
                if build_type == 'line':
                    build_name = build['data'].get('name', 'Unnamed Line')
                    if 'segment_index' in build['data']:
                        segment_idx = build['data'].get('segment_index', 0)
                        total_segments = build['data'].get('total_segments', 1)
                        job_info = QLabel(f"Line: {build_name} (Segment {segment_idx + 1}/{total_segments})")
                    else:
                        job_info = QLabel(f"Line: {build_name}")
                elif build_type == 'line_extension':
                    build_name = build['data'].get('line_name', 'Unnamed Line')
                    if 'segment_index' in build['data']:
                        segment_idx = build['data'].get('segment_index', 0)
                        total_segments = build['data'].get('total_segments', 1)
                        direction = "End" if build['data'].get('extend_from_end', True) else "Beginning"
                        job_info = QLabel(f"Line Extension: {build_name} ({direction}, Segment {segment_idx + 1}/{total_segments})")
                    else:
                        direction = "End" if build['data'].get('extend_from_end', True) else "Beginning"
                        job_info = QLabel(f"Line Extension: {build_name} ({direction})")
                elif build_type == 'line_upgrade':
                    build_name = build['data'].get('line_name', 'Unnamed Line')
                    upgrades = build['data'].get('upgrades', [])
                    upgrade_names = []
                    if 'overhead' in upgrades:
                        upgrade_names.append('Overhead Wire')
                    if 'thirdrail' in upgrades:
                        upgrade_names.append('Third Rail')
                    upgrade_str = ' + '.join(upgrade_names) if upgrade_names else 'Unknown'
                    job_info = QLabel(f"Line Upgrade: {build_name} ({upgrade_str})")
                else:
                    station_name = build['data'].get('name', 'Unnamed Station')
                    job_info = QLabel(f"Station: {station_name}")
                
                job_info.setStyleSheet("font-weight: bold;")
                job_layout.addWidget(job_info)
                
                
                assigned_workers = self.operations.get_build_assigned_workers(idx)
                
                if turns_remaining is None:
                    turns_display = "N/A"
                else:
                    work_remaining = build.get('work_remaining')
                    if work_remaining is not None and assigned_workers > 0:
                        import math
                        actual_turns = max(1, int(math.ceil(work_remaining / assigned_workers)))
                        turns_display = f"{actual_turns} (with {assigned_workers} workers)"
                    else:
                        turns_display = f"{turns_remaining} (no workers assigned)"
                progress_info = QLabel(f"Player: {player_name} | Turns Remaining: {turns_display} | Workers: {assigned_workers}")
                job_layout.addWidget(progress_info)
                
                
                worker_layout = QHBoxLayout()
                worker_label = QLabel("Assign Workers:")
                worker_spin = QSpinBox()
                worker_spin.setMinimum(0)
                total_rented = self.operations.get_player_construction_worker_count(build_player)
                total_assigned_elsewhere = 0
                for other_idx, other_build in enumerate(build_queue):
                    other_player = other_build.get('player')
                    if other_player and self.operations.get_player_id(other_player) == player_id and other_idx != idx:
                        total_assigned_elsewhere += self.operations.get_build_assigned_workers(other_idx)
                available_workers = total_rented - total_assigned_elsewhere
                worker_spin.setMaximum(max(0, available_workers))
                worker_spin.setValue(assigned_workers)
                worker_spin.valueChanged.connect(lambda val, i=idx: self.on_assign_workers(i, val))
                worker_layout.addWidget(worker_label)
                worker_layout.addWidget(worker_spin)
                if available_workers < assigned_workers:
                    worker_layout.addWidget(QLabel(f"(Available: {available_workers}/{total_rented}, {assigned_workers - available_workers} unassigned)"))
                else:
                    worker_layout.addWidget(QLabel(f"(Available: {available_workers}/{total_rented})"))
                worker_layout.addStretch()
                job_layout.addLayout(worker_layout)
                
                
                button_layout = QHBoxLayout()
                
                
                if idx > 0:
                    move_up_btn = QPushButton("Move Up")
                    move_up_btn.clicked.connect(lambda checked, i=idx: self.on_move_build_up(i))
                    button_layout.addWidget(move_up_btn)
                
                
                if idx < len(build_queue) - 1:
                    move_down_btn = QPushButton("Move Down")
                    move_down_btn.clicked.connect(lambda checked, i=idx: self.on_move_build_down(i))
                    button_layout.addWidget(move_down_btn)
                
                
                cancel_btn = QPushButton("Cancel")
                cancel_btn.setStyleSheet("background-color: #ff4444; color: white;")
                cancel_btn.clicked.connect(lambda checked, i=idx: self.on_cancel_build(i))
                button_layout.addWidget(cancel_btn)
                
                button_layout.addStretch()
                job_layout.addLayout(button_layout)
                
                
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)
                job_layout.addWidget(separator)
                
                in_progress_layout.addWidget(job_widget)
            
            in_progress_layout.addStretch()
            
            completed_label = QLabel("Completed Construction Jobs:")
            completed_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
            complete_jobs_layout.addWidget(completed_label)
            
            completed_jobs = self.operations.get_completed_jobs()
            if completed_jobs is None:
                completed_jobs = []
            else:
                completed_jobs = list(completed_jobs)
            player_completed_jobs = [job for job in completed_jobs 
                                   if job.get('player') and self.operations.get_player_id(job.get('player')) == player_id]
            
            if not player_completed_jobs:
                no_completed_label = QLabel("No completed jobs yet.")
                complete_jobs_layout.addWidget(no_completed_label)
            else:
                for completed_job in player_completed_jobs:
                        job_type = completed_job.get('type')
                        if not job_type:
                            continue
                        completed_year = completed_job.get('completed_year', 'Unknown')
                        
                        job_widget = QWidget()
                        job_layout = QVBoxLayout()
                        job_widget.setLayout(job_layout)
                        
                        if job_type == 'line':
                            build_name = completed_job.get('data', {}).get('name', 'Unnamed Line')
                            if 'segment_index' in completed_job.get('data', {}):
                                segment_idx = completed_job.get('data', {}).get('segment_index', 0)
                                total_segments = completed_job.get('data', {}).get('total_segments', 1)
                                job_info = QLabel(f"Line: {build_name} (Segment {segment_idx + 1}/{total_segments})")
                            else:
                                job_info = QLabel(f"Line: {build_name}")
                        else:
                            station_name = completed_job.get('data', {}).get('name', 'Unnamed Station')
                            job_info = QLabel(f"Station: {station_name}")
                        
                        job_info.setStyleSheet("font-weight: bold;")
                        job_layout.addWidget(job_info)
                        
                        completion_info = QLabel(f"Completed: Year {completed_year}")
                        job_layout.addWidget(completion_info)
                        
                        if job_type == 'line':
                            sell_btn = QPushButton("Sell Line to Marketplace")
                            def sell_line_to_marketplace():
                                current_player = self.operations.get_current_player(self.current_player_index)
                                if not current_player:
                                    QMessageBox.warning(self, "Error", "No active player.")
                                    return
                                
                                line_name = completed_job.get('data', {}).get('name', '')
                                if not line_name:
                                    QMessageBox.warning(self, "Error", "Line name not found.")
                                    return
                                
                                game_map = self.operations.get_map()
                                line = game_map.get_lines_by_name().get(line_name)
                                
                                if not line:
                                    QMessageBox.warning(self, "Error", f"Line '{line_name}' not found on map.")
                                    return
                                
                                owner = self.operations.get_line_owner(line)
                                if owner != current_player:
                                    QMessageBox.warning(self, "Error", f"You do not own line '{line_name}'.")
                                    return
                                
                                reply = QMessageBox.question(self, "Sell Line", 
                                                             f"Are you sure you want to sell line '{line_name}' to the marketplace?",
                                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                                if reply == QMessageBox.StandardButton.Yes:
                                    self.operations.add_line_to_marketplace(line)
                                    if line_name in current_player.get_line_names():
                                        current_player.get_line_names().remove(line_name)
                                    game_map.lines.pop(line_name, None)
                                    QMessageBox.information(self, "Line Sold", f"Line '{line_name}' has been listed on the marketplace.")
                                    self.refresh_construction_tab()
                                    self.DisplayMapInfo()
                                    self.DisplayLinesControls(current_player)
                            
                            sell_btn.clicked.connect(sell_line_to_marketplace)
                            job_layout.addWidget(sell_btn)
                        
                        separator = QFrame()
                        separator.setFrameShape(QFrame.Shape.HLine)
                        separator.setFrameShadow(QFrame.Shadow.Sunken)
                        job_layout.addWidget(separator)
                        
                        complete_jobs_layout.addWidget(job_widget)
            
            complete_jobs_layout.addStretch()
    
    def on_assign_workers(self, build_index: int, num_workers: int):
        
        try:
            self.operations.assign_workers_to_build(build_index, num_workers)
            # Don't auto-assign unassigned workers here - let user have full control
            # Auto-assignment only happens when workers are rented or at turn start
            self.refresh_construction_tab()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to assign workers: {str(e)}")
    
    def on_move_build_up(self, build_index: int):
        
        try:
            self.operations.reorder_build_queue(build_index, build_index - 1)
            self.refresh_construction_tab()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to move build: {str(e)}")
    
    def on_move_build_down(self, build_index: int):
        try:
            self.operations.reorder_build_queue(build_index, build_index + 1)
            self.refresh_construction_tab()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to move build: {str(e)}")
    
    def on_cancel_build(self, build_index: int):
        reply = QMessageBox.question(self, "Cancel Build", 
                                     "Are you sure you want to cancel this build? You will receive a 50% refund.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                result = self.operations.cancel_build(build_index)
                QMessageBox.information(self, "Build Cancelled", 
                                       f"Build cancelled. Refund: ${result['refund']}")
                self.refresh_construction_tab()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to cancel build: {str(e)}")


    def _toggle_population_point(self, x: int, y: int):
        """Toggle a population point at (x, y) - add if missing, remove if present.
        Updates the appropriate INCLUDED_POINTS list permanently.
        Works generically with any map preset that follows the standard naming convention."""
        if self.operations is None:
            return
        
        game_map = self.operations.get_map()
        if not game_map:
            return
        
        # Use generic utility to detect preset file and variable name
        from map_preset_utils import get_preset_file_info, read_included_points_from_file, write_included_points_to_file
        
        preset_info = get_preset_file_info(game_map)
        if not preset_info:
            QMessageBox.warning(self, "Edit Mode", "Could not detect map preset file. Edit mode is only available for maps with preset files.")
            return
        
        file_path, variable_name = preset_info
        
        # Verify file exists and is writable
        import os
        if not os.path.exists(file_path):
            QMessageBox.warning(self, "Edit Mode", f"Preset file not found: {file_path}")
            return
        if not os.access(file_path, os.W_OK):
            QMessageBox.warning(self, "Edit Mode", f"Preset file is not writable: {file_path}")
            return
        
        # Ensure coordinates are integers (points in file are stored as integers)
        x = int(round(x))
        y = int(round(y))
        point = (x, y)
        
        print(f"DEBUG: Toggling point {point} (x={x}, y={y})")
        print(f"DEBUG: Target file: {file_path}")
        print(f"DEBUG: Target variable: {variable_name}")
        
        # Read current points from file - FORCE fresh read by clearingany caches
        import sys
        import importlib
        import os
        # Clear module cache to ensure fresh read
        module_name = os.path.basename(file_path).replace('.py', '')
        if module_name in sys.modules:
            del sys.modules[module_name]
            print(f"DEBUG: Cleared {module_name} from module cache")
        
        # Read fresh from file (read multiple times to ensure we get latest)
        existing_points = None
        for attempt in range(3):
            existing_points = read_included_points_from_file(file_path, variable_name)
            if existing_points is not None:
                break
            import time
            time.sleep(0.01)
        
        if existing_points is None:
            existing_points = set()
            print(f"DEBUG: Failed to read points from file, using empty set")
        
        # Ensure all points are tuples of integers (normalize format)
        normalized_points = set()
        for p in existing_points:
            if isinstance(p, (tuple, list)) and len(p) >= 2:
                px, py = int(round(p[0])), int(round(p[1]))
                normalized_points.add((px, py))
        existing_points = normalized_points
        
        # Remove any duplicates (set already does this, but be explicit)
        existing_points = set(existing_points)
        
        # CRITICAL: Check if point is currently VISIBLE on the map
        # If a point is visible (has population > 0), it MUST be in INCLUDED_POINTS
        # This is more reliable than just checking the file, because the file might have stale data
        point_population = game_map.population.get(point, 0)
        point_is_visible = point_population > 0
        
        # Also check file directly
        point_in_file = point in existing_points
        
        # Additional check: see if any similar points exist (for debugging)
        similar_points = [p for p in existing_points if abs(p[0] - x) <= 1 and abs(p[1] - y) <= 1 and p != point]
        exact_matches = [p for p in existing_points if p == point]
        
        print(f"DEBUG: File has {len(existing_points)} points before toggle")
        print(f"DEBUG: Point {point} - population: {point_population}, visible: {point_is_visible}, in file: {point_in_file}")
        print(f"DEBUG: Exact matches in file: {len(exact_matches)}")
        if similar_points:
            print(f"DEBUG: Found {len(similar_points)} similar points near {point}: {similar_points}")
        
        # DECISION LOGIC: If point is VISIBLE, it should be REMOVED
        # If point is NOT visible, it should be ADDED
        # Use visibility as primary check (more reliable than file read)
        if point_is_visible:
            # Point is visible - it MUST be in the file, so REMOVE it
            point_exists = True
            print(f"DEBUG: Point {point} is VISIBLE (pop={point_population}) - will REMOVE it")
            # Verify it's actually in the file - if not, try fuzzy matching
            if not point_in_file:
                print(f"DEBUG: WARNING - Point is visible but not in file! Trying fuzzy match...")
                # Try to find it with fuzzy matching
                if similar_points:
                    closest = min(similar_points, key=lambda p: abs(p[0] - x) + abs(p[1] - y))
                    print(f"DEBUG: Using closest point {closest} from file instead of {point}")
                    point = closest  # Update point to the closest match
                    point_in_file = point in existing_points  # Re-check with updated point
                    if point_in_file:
                        print(f"DEBUG: Closest match {point} is in file - will remove it")
                    else:
                        print(f"DEBUG: ERROR - Closest match {point} still not in file!")
                else:
                    print(f"DEBUG: ERROR - No similar points found! Point {point} is visible but not in file.")
        else:
            # Point is NOT visible - it should NOT be in the file, so ADD it
            point_exists = False
            print(f"DEBUG: Point {point} is NOT visible (pop={point_population}) - will ADD it")
            # But check file to be sure
            if point_in_file:
                print(f"DEBUG: WARNING - Point not visible but found in file! Will remove it instead.")
                point_exists = True
        
        # Store previous state for undo
        previous_state = {
            'map_type': variable_name.replace('_INCLUDED_POINTS', '').lower(),
            'point': point,
            'was_present': point_exists,
            'previous_population': game_map.population.get(point, 0)
        }
        
        # Toggle the point based on whether it EXISTS (visible or in file)
        if point_exists:
            # Point exists - REMOVE it
            # At this point, 'point' has been updated to the correct value (possibly via fuzzy matching)
            if point in existing_points:
                existing_points.remove(point)
                print(f"DEBUG: Successfully removed {point} from existing_points set")
            else:
                print(f"DEBUG: ERROR - Point {point} not in existing_points! This should not happen.")
                # Try one more time with the original coordinates
                original_point = (x, y)
                if original_point in existing_points:
                    existing_points.remove(original_point)
                    point = original_point
                    print(f"DEBUG: Removed using original coordinates {point}")
                else:
                    print(f"DEBUG: CRITICAL ERROR - Cannot remove point {point} or {original_point} from file!")
            
            action = "removed"
            print(f"DEBUG: REMOVING point {point} from file (was present/visible). Set now has {len(existing_points)} points.")
        else:
            # Point doesn't exist - ADD it
            existing_points.add(point)
            action = "added"
            print(f"DEBUG: ADDING point {point} to file (was not present/visible). Set now has {len(existing_points)} points.")
        
        # Ensure no duplicates before writing
        existing_points = set(existing_points)  # Deduplicate
        print(f"DEBUG: After toggle, will write {len(existing_points)} points to file")
        
        # IMMEDIATELY write to file - this is the source of truth
        # Write synchronously with file system sync to ensure it's written immediately
        success = write_included_points_to_file(file_path, variable_name, existing_points)
        if not success:
            QMessageBox.warning(self, "Edit Mode", f"Failed to write changes to {file_path}")
            return
        
        print(f"DEBUG: Immediately wrote {action} point {point} to {file_path}. File now has {len(existing_points)} points.")
        
        # VERIFY the write by reading back immediately
        import time
        time.sleep(0.02)  # Small delay for file system
        # Clear module cache again before verification read
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        verify_points = read_included_points_from_file(file_path, variable_name)
        if verify_points is None:
            verify_points = set()
            print(f"DEBUG: WARNING - Could not read file for verification!")
        
        # Normalize verification points
        verify_points = set((int(round(p[0])), int(round(p[1]))) for p in verify_points if isinstance(p, (tuple, list)) and len(p) >= 2)
        
        print(f"DEBUG: Verification - File now contains {len(verify_points)} points")
        print(f"DEBUG: Verification - Expected {len(existing_points)} points after {action}")
        if len(verify_points) != len(existing_points):
            print(f"DEBUG: WARNING - Point count mismatch! Expected {len(existing_points)}, got {len(verify_points)}")
        
        point_verified = point in verify_points
        if action == "removed":
            if point_verified:
                print(f"ERROR: Point {point} still in file after removal! Verification failed.")
                print(f"DEBUG: File contents: {sorted(list(verify_points))[:10]}..." if len(verify_points) > 10 else f"DEBUG: File contents: {sorted(list(verify_points))}")
                QMessageBox.warning(self, "Edit Mode", f"Failed to remove point {point} from file. Please try again.")
                return
            else:
                print(f"DEBUG: ✓ Verified removal - point {point} not in file (file has {len(verify_points)} points)")
        else:  # added
            if not point_verified:
                print(f"ERROR: Point {point} not in file after addition! Verification failed.")
                print(f"DEBUG: File contents: {sorted(list(verify_points))[:10]}..." if len(verify_points) > 10 else f"DEBUG: File contents: {sorted(list(verify_points))}")
                QMessageBox.warning(self, "Edit Mode", f"Failed to add point {point} to file. Please try again.")
                return
            else:
                print(f"DEBUG: ✓ Verified addition - point {point} is in file (file has {len(verify_points)} points)")
        
        # Check for duplicates in verification and clean them up
        if len(verify_points) != len(set(verify_points)):
            print(f"WARNING: Found duplicates in file! Cleaning up...")
            from map_preset_utils import remove_duplicates_from_file
            remove_duplicates_from_file(file_path, variable_name)
            # Re-read after cleanup
            if module_name in sys.modules:
                del sys.modules[module_name]
            verify_points = read_included_points_from_file(file_path, variable_name)
            if verify_points is None:
                verify_points = set()
            verify_points = set((int(round(p[0])), int(round(p[1]))) for p in verify_points if isinstance(p, (tuple, list)) and len(p) >= 2)
            print(f"DEBUG: After cleanup, file has {len(verify_points)} unique points")
        
        # IMMEDIATELY update the game map population to match the file
        # CRITICAL: We must update the population dictionary to reflect the file state RIGHT NOW
        if action == "removed":
            # COMPLETELY REMOVE the point from the population dictionary IMMEDIATELY
            # This ensures it disappears from the display right away
            old_pop = game_map.population.get(point, 0)
            if point in game_map.population:
                del game_map.population[point]
            # Also set to 0 as backup (in case it gets re-added somehow)
            game_map.population[point] = 0
            # Mark as deleted so update_population_over_time knows to skip it
            if not hasattr(game_map, '_deleted_population_points'):
                game_map._deleted_population_points = set()
            game_map._deleted_population_points.add(point)
            print(f"DEBUG: Immediately removed point {point} from population dictionary (was {old_pop})")
        else:
            # Set a default population if adding - need to detect which preset function to use
            # Extract x, y from point tuple
            x, y = point[0], point[1]
            if hasattr(game_map, 'is_nyc_proper') and game_map.is_nyc_proper:
                from map_presets_nyc import get_nyc_population_at_location, get_nyc_bounds
                bounds = get_nyc_bounds()
                lat = bounds['min_lat'] + (y / game_map.height) * (bounds['max_lat'] - bounds['min_lat'])
                lon = bounds['min_lon'] + (x / game_map.width) * (bounds['max_lon'] - bounds['min_lon'])
                pop = get_nyc_population_at_location(lat, lon, False)
                game_map.population[point] = pop
            elif hasattr(game_map, 'is_ny_metro') and game_map.is_ny_metro:
                from map_presets_ny_metro import get_ny_metro_population_at_location, get_ny_metro_bounds
                bounds = get_ny_metro_bounds()
                lat = bounds['min_lat'] + (y / game_map.height) * (bounds['max_lat'] - bounds['min_lat'])
                lon = bounds['min_lon'] + (x / game_map.width) * (bounds['max_lon'] - bounds['min_lon'])
                pop = get_ny_metro_population_at_location(lat, lon, False)
                game_map.population[point] = pop
            else:
                # Generic fallback - set a default population
                game_map.population[point] = 100
        
        # CRITICAL: Reload the appropriate module IMMEDIATELY to clear any cached values
        # This ensures that if any code tries to import the variable, it gets the fresh value
        import importlib
        import sys
        if hasattr(game_map, 'is_nyc_proper') and game_map.is_nyc_proper:
            # Remove from cache and reload IMMEDIATELY
            if 'map_presets_nyc' in sys.modules:
                del sys.modules['map_presets_nyc']
            import map_presets_nyc
            importlib.reload(map_presets_nyc)
            print(f"DEBUG: Immediately reloaded map_presets_nyc module after {action} point {point}")
        elif hasattr(game_map, 'is_ny_metro') and game_map.is_ny_metro:
            # Remove from cache and reload IMMEDIATELY
            if 'map_presets_ny_metro' in sys.modules:
                del sys.modules['map_presets_ny_metro']
            import map_presets_ny_metro
            importlib.reload(map_presets_ny_metro)
            print(f"DEBUG: Immediately reloaded map_presets_ny_metro module after {action} point {point}")
        
        # Add to edit history for undo
        if not hasattr(self, 'edit_history'):
            self.edit_history = []
        self.edit_history.append({
            'previous_state': previous_state,
            'file_path': file_path,
            'variable_name': variable_name,
            'points_list': sorted(existing_points)
        })
        
        # Enable undo button
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            undo_btn = player_tabs_dict.get('undo_btn')
            if undo_btn:
                undo_btn.setEnabled(True)
        
        # IMMEDIATELY refresh the map display - this will read from the file we just wrote
        # This ensures the point disappears from the visual display right away
        self.DisplayMapInfo()
        print(f"DEBUG: Immediately refreshed map display after {action} point {point}")
    
    def _undo_last_edit(self):
        """Undo the last population point edit."""
        if not hasattr(self, 'edit_history') or not self.edit_history:
            QMessageBox.information(self, "Undo", "No edits to undo.")
            return
        
        if self.operations is None:
            return
        
        game_map = self.operations.get_map()
        if not game_map:
            return
        
        # Get the last edit
        last_edit = self.edit_history.pop()
        previous_state = last_edit['previous_state']
        file_path = last_edit['file_path']
        point = previous_state['point']
        was_present = previous_state['was_present']
        previous_population = previous_state['previous_population']
        map_type = previous_state['map_type']
        
        import re
        import os
        
        # Read the file
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Restore the previous state
        if map_type == 'nyc':
            pattern = r'NYC_INCLUDED_POINTS\s*=\s*\[.*?\]'
            var_name = 'NYC_INCLUDED_POINTS'
            match = re.search(r'NYC_INCLUDED_POINTS\s*=\s*\[(.*?)\]', content, re.DOTALL)
        else:  # ny_metro
            pattern = r'NY_METRO_INCLUDED_POINTS\s*=\s*\[.*?\]'
            var_name = 'NY_METRO_INCLUDED_POINTS'
            match = re.search(r'NY_METRO_INCLUDED_POINTS\s*=\s*\[(.*?)\]', content, re.DOTALL)
        
        if match:
            existing_points_str = match.group(1).strip()
            if existing_points_str:
                existing_points = eval(f"[{existing_points_str}]")
            else:
                existing_points = []
        else:
            existing_points = []
        
        # Restore the point to its previous state
        if was_present:
            # Point was present before, restore it
            if point not in existing_points:
                existing_points.append(point)
            game_map.population[point] = previous_population
        else:
            # Point was not present before, remove it
            if point in existing_points:
                existing_points.remove(point)
            game_map.population[point] = 0
        
        # Update the file
        all_points_list = sorted(existing_points)
        points_str = "[\n" + ",\n".join(f"    {p}" for p in all_points_list) + "\n]"
        new_content = re.sub(pattern, f'{var_name} = {points_str}', content, flags=re.DOTALL)
        
        with open(file_path, 'w') as f:
            f.write(new_content)
        
        # Update undo button state
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            undo_btn = player_tabs_dict.get('undo_btn')
            if undo_btn:
                undo_btn.setEnabled(len(self.edit_history) > 0)
        
        # Refresh the map
        self.DisplayMapInfo()


if __name__ == "__main__":
    import os
    from PyQt6.QtCore import Qt
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec())
