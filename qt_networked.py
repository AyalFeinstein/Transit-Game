import sys
import pickle
import os
import tempfile
import logging
from pathlib import Path
import warnings

logger = logging.getLogger(__name__)

warnings.filterwarnings('ignore', category=DeprecationWarning, message='.*sipPyTypeDict.*')

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (
        QWebEngineSettings, QWebEngineProfile, QWebEnginePage,
        QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestInfo,
    )
    from PyQt6.QtCore import QUrl, QDir
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    QWebEngineView = None
    QWebEngineSettings = None
    QWebEnginePage = None
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
    QTabBar,
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
    QListWidgetItem,
    QSizePolicy,
    QTextBrowser,
    QMenu,
    QSplitter,
    QStackedWidget,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtGui import QColor, QPalette, QPainter, QPen, QBrush, QFont, QAction, QPixmap, QIcon
from PyQt6.QtCore import Qt, QRectF, QPointF, QObject, QEvent
from operations import Operations
from game_presets import STARTING_MONEY
from inflation_utils import get_inflation_factor
# Vehicle imports removed - using dynamic registry system instead
from vehicle import Locomotive, Railcar, Trainset, Ferry, Bus

# Maximum time (ms) for line creation and map refresh - prevents long freezes
LINE_CREATION_MAX_MS = 15000
LINE_EXTENSION_MAX_MS_PER_SEGMENT = 5000
MAP_REFRESH_MAX_MS = 15000
MAP_DISPLAY_MAX_ROADS = 400

# Save games directory (relative to project root)
SAVEGAMES_DIR = Path(__file__).resolve().parent / "savegames"
APP_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "locologo.png"
APP_SPLASH_PATH = Path(__file__).resolve().parent / "assets" / "splash.png"


def application_window_icon():
    """Application logo as QIcon (empty if assets/locologo.png is missing)."""
    from PyQt6.QtGui import QIcon
    if APP_LOGO_PATH.is_file():
        return QIcon(str(APP_LOGO_PATH))
    return QIcon()


def apply_application_logo(widget):
    """Set window icon from application logo when the asset exists."""
    icon = application_window_icon()
    if not icon.isNull():
        widget.setWindowIcon(icon)


def _configure_tab_scroll(scroll: QScrollArea) -> None:
    """Apply shared scroll-area settings for tab bodies."""
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


def _make_tab_body_scroll() -> tuple:
    """Create a QScrollArea with an inner QWidget/VBoxLayout for tab body content."""
    scroll = QScrollArea()
    _configure_tab_scroll(scroll)
    body = QWidget()
    body_layout = QVBoxLayout()
    body.setLayout(body_layout)
    scroll.setWidget(body)
    return scroll, body_layout


def _wrap_settings_tab_scroll(content: QWidget) -> QScrollArea:
    """Wrap a settings tab body so long forms scroll instead of clipping."""
    scroll = QScrollArea()
    _configure_tab_scroll(scroll)
    scroll.setWidget(content)
    return scroll


def _parse_rules_markdown_sections(content: str):
    """Split RULES.md by top-level ## headings (not ###). Returns [(title, body), ...]."""
    lines = content.splitlines()
    sections = []
    preamble = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('## ') and not line.startswith('###'):
            break
        preamble.append(line)
        i += 1
    preamble_text = '\n'.join(preamble).strip()
    if preamble_text:
        sections.append(('Introduction', preamble_text))
    current_title = None
    current_lines = []
    while i < len(lines):
        line = lines[i]
        if line.startswith('## ') and not line.startswith('###'):
            if current_title is not None:
                sections.append((current_title, '\n'.join(current_lines).strip()))
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
        i += 1
    if current_title is not None:
        sections.append((current_title, '\n'.join(current_lines).strip()))
    if not sections and content.strip():
        return [('Rules', content.strip())]
    return sections


def _locomotive_is_self_contained(loco):
    """True when a locomotive is a self-contained unit (EMU/DMU/subway/etc.), not a loco+cars consist."""
    return loco is not None and not getattr(loco, 'can_have_railcars', True)


def _trainset_is_dmu_or_emu(trainset):
    """True if the trainset's locomotive is a DMU or EMU (used for marketplace trainset tab filter)."""
    loco = getattr(trainset, 'locomotive', None)
    if not loco:
        return False
    if _locomotive_is_self_contained(loco):
        return True
    name_lower = (getattr(loco, 'name', '') or '').lower()
    fuel = getattr(loco, 'fuel', None)
    fuel_name = getattr(fuel, '__class__', None).__name__ if fuel else ''
    if 'emu' in name_lower or 'interurban' in name_lower:
        return True
    if fuel_name == 'Oil' and ('multiple unit' in name_lower or 'railcar' in name_lower or 'dmu' in name_lower):
        return True
    return False


# Leaflet embedded content (loaded once, avoids CDN blocking in QWebEngineView)
_LEAFLET_CSS_CACHE = None
_LEAFLET_JS_CACHE = None


if WEBENGINE_AVAILABLE:
    class OSMUrlRequestInterceptor(QWebEngineUrlRequestInterceptor):
        """Add Accept-Language and User-Agent so tile servers accept requests (many block non-browser traffic)."""
        def interceptRequest(self, info):
            info.setHttpHeader(b"Accept-Language", b"en-US,en;q=0.9")
            # Use browser-like User-Agent; tile servers often block default Qt/Chromium identifiers
            info.setHttpHeader(b"User-Agent", b"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
else:
    OSMUrlRequestInterceptor = None

# Keep Leaflet in sync when the QWebEngineView or map pane resizes (pan/zoom/size).
# ensureMapContainerSize must run before invalidateSize — HTML sets explicit pixel
# dimensions on #map; QWebEngineView layout resizes do not fire window.resize in JS.
_MAP_ENSURE_VISIBLE_JS = (
    "(function(){"
    "if(typeof ensureMapContainerSize==='function'){ensureMapContainerSize();}"
    "if(typeof window.ensureMapVisible==='function'){window.ensureMapVisible();}"
    "else if(window.mapInstance){window.mapInstance.invalidateSize(true);}"
    "else if(typeof initMap==='function'){initMap();}"
    "})();"
)
_MAP_INVALIDATE_JS = _MAP_ENSURE_VISIBLE_JS


class _MapPaneResizeFilter(QObject):
    """Debounced ensureMapVisible when the map web view or container changes size."""

    def __init__(self, web_view, *extra_watched):
        super().__init__(web_view)
        self._web_view = web_view
        self._watched = {web_view, *extra_watched}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._invalidate)

    def eventFilter(self, watched, event):
        if watched in self._watched and event.type() == QEvent.Type.Resize:
            self._timer.start()
        return False

    def _invalidate(self):
        wv = self._web_view
        if wv is None:
            return
        try:
            if wv.parent() is None:
                return
            page = wv.page() if hasattr(wv, 'page') else None
            if page:
                page.runJavaScript(_MAP_ENSURE_VISIBLE_JS)
        except Exception:
            pass


class _MapWebViewFocusFilter(QObject):
    """Give the map web view focus on hover/click so Leaflet receives wheel events."""

    def __init__(self, web_view):
        super().__init__(web_view)
        self._web_view = web_view

    def eventFilter(self, watched, event):
        if watched is self._web_view and event.type() in (
            QEvent.Type.Enter,
            QEvent.Type.MouseButtonPress,
        ):
            try:
                watched.setFocus()
            except Exception:
                pass
        return False


def _install_map_web_view_resize_filter(web_view, map_container=None) -> None:
    if web_view is None:
        return
    filt = getattr(web_view, '_transit_resize_filter', None)
    if filt is None:
        filt = _MapPaneResizeFilter(web_view, map_container) if map_container else _MapPaneResizeFilter(web_view)
        web_view.installEventFilter(filt)
        web_view._transit_resize_filter = filt
    elif map_container is not None:
        filt._watched.add(map_container)
    if map_container is not None and getattr(map_container, '_transit_resize_filter', None) is None:
        map_container.installEventFilter(filt)
        map_container._transit_resize_filter = filt
    focus_filt = getattr(web_view, '_transit_focus_filter', None)
    if focus_filt is None:
        focus_filt = _MapWebViewFocusFilter(web_view)
        web_view.installEventFilter(focus_filt)
        web_view._transit_focus_filter = focus_filt


# Backward-compatible alias for tests
_MapWebViewResizeFilter = _MapPaneResizeFilter

# Singleton interceptor instance - must be kept alive to prevent GC (Qt requires this)
_OSM_INTERCEPTOR_INSTANCE = None

def _install_osm_tile_interceptor():
    """Install OSM tile request interceptor on default profile at app startup.
    Must be called before any QWebEngineView is created. Prevents grey map background."""
    global _OSM_INTERCEPTOR_INSTANCE
    if not WEBENGINE_AVAILABLE or OSMUrlRequestInterceptor is None:
        return
    if _OSM_INTERCEPTOR_INSTANCE is None:
        _OSM_INTERCEPTOR_INSTANCE = OSMUrlRequestInterceptor()
    try:
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpUserAgent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        profile.setHttpAcceptLanguage("en-US,en;q=0.9")
        profile.setUrlRequestInterceptor(_OSM_INTERCEPTOR_INSTANCE)
    except Exception as e:
        logger.warning("Could not install OSM tile interceptor: %s", e)


def _get_map_html_file_path(player_id: int) -> Path:
    """Return path for map HTML file. Loading from local file enables OSM tile requests (LocalContentCanAccessRemoteUrls)."""
    return Path(tempfile.gettempdir()) / f"transitgame_map_{player_id}.html"


def _write_map_html_for_osm(player_id: int, html_content: str) -> Path | None:
    """Write map HTML to a local file. Returns path if successful, None otherwise.
    Always use file-based load so OSM tiles display; never fall back to setHtml which blocks tiles."""
    for path in [
        _get_map_html_file_path(player_id),
        Path(__file__).resolve().parent / f"transitgame_map_{player_id}.html",
    ]:
        try:
            path.write_text(html_content, encoding="utf-8")
            return path
        except Exception as e:
            continue
    return None


def _load_map_from_file(web_view, html_content: str, player_id: int, on_load_finished=None):
    """Load map HTML from local file so OSM tiles display. setHtml blocks tile requests (grey map)."""
    path = _write_map_html_for_osm(player_id, html_content)
    if path is not None:
        if on_load_finished is not None:
            web_view.loadFinished.connect(on_load_finished)
        import time
        url = QUrl.fromLocalFile(str(path))
        url.setQuery(f"t={int(time.time() * 1000)}")  # Cache-bust so reloads get fresh tiles
        web_view.load(url)
    else:
        # Fallback if file write fails - tiles may be grey
        if on_load_finished is not None:
            web_view.loadFinished.connect(on_load_finished)
        web_view.setHtml(html_content, QUrl("https://example.com/"))


def _create_map_web_view_profile():
    """Create a QWebEngineProfile configured for map tile loading. Profile must exist before any load."""
    if not WEBENGINE_AVAILABLE or QWebEngineProfile is None:
        return None
    profile = QWebEngineProfile("TransitGameMap", None)  # Named profile for persistent cache
    profile.setHttpUserAgent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    profile.setHttpAcceptLanguage("en-US,en;q=0.9")
    if OSMUrlRequestInterceptor is not None:
        profile.setUrlRequestInterceptor(OSMUrlRequestInterceptor())
    return profile


def _get_embedded_leaflet():
    """Load Leaflet CSS and JS from static files. Embedded to avoid QWebEngineView blocking CDN."""
    global _LEAFLET_CSS_CACHE, _LEAFLET_JS_CACHE
    if _LEAFLET_CSS_CACHE is None or _LEAFLET_JS_CACHE is None:
        static_dir = Path(__file__).resolve().parent / "static"
        css_path = static_dir / "leaflet.css"
        js_path = static_dir / "leaflet.js"
        try:
            _LEAFLET_CSS_CACHE = css_path.read_text(encoding="utf-8", errors="replace")
            _LEAFLET_JS_CACHE = js_path.read_text(encoding="utf-8", errors="replace")
            # Escape for embedding: </script> and </style> would close tags
            _LEAFLET_JS_CACHE = _LEAFLET_JS_CACHE.replace("</script>", "<\\/script>")
            _LEAFLET_CSS_CACHE = _LEAFLET_CSS_CACHE.replace("</style>", "<\\/style>")
        except Exception as e:
            logger.warning("Could not load embedded Leaflet: %s", e)
            _LEAFLET_CSS_CACHE = ""
            _LEAFLET_JS_CACHE = ""
    return _LEAFLET_CSS_CACHE, _LEAFLET_JS_CACHE


class AIInYearTurnsThread(QThread):
    """Runs in-year AI turns in the background while humans play the same calendar year."""
    finished_turns = pyqtSignal(object, object)  # ai_results_list, ai_build_failures
    ai_progress = pyqtSignal(str, str)

    def __init__(self, operations):
        super().__init__()
        self.operations = operations

    def _progress_cb(self, player_name: str, action_name: str):
        self.ai_progress.emit(player_name, action_name)

    def run(self):
        from ai import run_pending_ai_in_year_turns, AI_TURN_TIME_BUDGET_SEC
        pending = self.operations.get_ai_players_needing_in_year_turn()
        ai_results_list, ai_build_failures = run_pending_ai_in_year_turns(
            self.operations,
            progress_callback=self._progress_cb,
            players=pending,
            deadline_sec=AI_TURN_TIME_BUDGET_SEC,
        )
        self.finished_turns.emit(ai_results_list, ai_build_failures)


class AdvanceYearThread(QThread):
    """Runs any remaining AI turns, then advance_year_complete (end-year sequence)."""
    finished_with_result = pyqtSignal(object, object, bool, str, object)  # result, ai_results_list, error_occurred, error_message, ai_build_failures
    ai_progress = pyqtSignal(str, str)  # player_name, action_name - for investigative mode

    def __init__(self, operations, old_year):
        super().__init__()
        self.operations = operations
        self.old_year = old_year

    def _progress_cb(self, player_name: str, action_name: str):
        self.ai_progress.emit(player_name, action_name)

    def run(self):
        from ai import (
            run_ai_turn,
            AIIdleTurnError,
            validate_ai_turn_results,
            run_pending_ai_in_year_turns,
            AI_TURN_TIME_BUDGET_SEC,
        )
        progress_cb = self._progress_cb
        pending = self.operations.get_ai_players_needing_in_year_turn()
        deadline_sec = AI_TURN_TIME_BUDGET_SEC
        ai_results_list, ai_build_failures = run_pending_ai_in_year_turns(
            self.operations,
            progress_callback=progress_cb,
            players=pending,
            deadline_sec=deadline_sec,
        )
        result = None
        error_occurred = False
        error_message = ""
        if not self.operations.all_players_completed_year_actions():
            error_occurred = True
            error_message = "Cannot advance year until every player has finished (AIs and humans)."
        else:
            try:
                result = self.operations.advance_year_complete(self.old_year)
            except Exception as e:
                import traceback
                traceback.print_exc()
                error_occurred = True
                error_message = str(e)
        # Run AI for newly spawned player in the same round (same as sync path)
        if result is not None and result.get('new_ai_spawned'):
            new_info = result['new_ai_spawned']
            new_player = new_info.get('player') if isinstance(new_info, dict) else None
            if new_player is not None:
                try:
                    acts = run_ai_turn(
                        self.operations, new_player,
                        progress_callback=progress_cb,
                        deadline_sec=deadline_sec,
                    )
                    validate_ai_turn_results(self.operations, new_player, acts)
                    self.operations.mark_ai_in_year_turn_completed(new_player)
                    ai_results_list.append((new_player, acts))
                except AIIdleTurnError as e:
                    ai_results_list.append((new_player, []))
                    ai_build_failures.append((new_player.get_name(), str(e)))
                except Exception as e:
                    self.operations.mark_ai_in_year_turn_completed(new_player)
                    ai_results_list.append((new_player, []))
                    ai_build_failures.append((new_player.get_name(), str(e)))
        if result is not None:
            result['ai_build_failures'] = ai_build_failures
        self.finished_with_result.emit(result, ai_results_list, error_occurred, error_message, ai_build_failures)


from player_colors import (
    PLAYER_COLORS,
    PLAYER_COLORS_HEX,
    NO_ALLEGIANCE_COLOR,
    get_player_color_index,
)


def player_color_icon(color_index: int, size: int = 10) -> QIcon:
    """Small square icon in the player's assigned color."""
    pm = QPixmap(size, size)
    idx = max(0, int(color_index)) % len(PLAYER_COLORS)
    pm.fill(PLAYER_COLORS[idx])
    return QIcon(pm)


def create_player_color_swatch(color_index: int, size: int = 10) -> QLabel:
    """Small colored square for a player."""
    swatch = QLabel()
    swatch.setFixedSize(size, size)
    idx = max(0, int(color_index)) % len(PLAYER_COLORS_HEX)
    color = PLAYER_COLORS_HEX[idx]
    swatch.setStyleSheet(f"background-color: {color}; border: 1px solid #666666;")
    return swatch


def create_player_color_name_row(color_index: int, name: str) -> QWidget:
    """Colored square with player name (for legends and lists)."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.setSpacing(4)
    layout.addWidget(create_player_color_swatch(color_index))
    label = QLabel(name)
    label.setWordWrap(False)
    layout.addWidget(label)
    return row


def create_status_player_money_widget():
    """Status-bar row: colored square + name/money label."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    swatch = create_player_color_swatch(0)
    text = QLabel("")
    text.setWordWrap(False)
    text.setMinimumWidth(72)
    text.setMaximumWidth(200)
    text.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
    layout.addWidget(swatch)
    layout.addWidget(text)
    row.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    row.setFixedHeight(max(16, swatch.sizeHint().height() + 2))
    return row, swatch, text


def _elide_status_label(text: QLabel, full: str) -> None:
    text.setToolTip(full)
    available = max(text.width(), text.minimumWidth(), 72)
    available = min(available, text.maximumWidth() if text.maximumWidth() > 0 else available)
    text.setText(text.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, available))


def set_status_player_money_display(swatch: QLabel, text: QLabel, color_index: int, name: str, money, row: QWidget = None) -> None:
    idx = max(0, int(color_index)) % len(PLAYER_COLORS_HEX)
    color = PLAYER_COLORS_HEX[idx]
    swatch.setStyleSheet(f"background-color: {color}; border: 1px solid #666666;")
    if name:
        full = f"{name}: ${money}"
        text.setMinimumWidth(min(text.fontMetrics().horizontalAdvance(full) + 4, text.maximumWidth()))
        _elide_status_label(text, full)
        swatch.setVisible(True)
        if row is not None:
            row.setVisible(True)
    else:
        text.setToolTip("")
        text.setText("")
        swatch.setVisible(False)
        if row is not None:
            row.setVisible(False)


class GovernmentChamberWidget(QWidget):
    """Inverted semicircle chamber (∪-shaped): 5 rows of 10 seats each, arc opening upward, leader at bottom center.
    Seats show allegiance (player color or light grey). Click or right-click to lobby."""
    NUM_SEATS = 50
    NUM_ROWS = 5
    SEATS_PER_ROW = 10

    def __init__(self, seats_data: list, on_lobby_seat, on_lobby_leader, seats_with_allegiance: list = None, player_names: list = None, player_color_by_id: dict = None, parent=None):
        super().__init__(parent)
        self.seats_data = seats_data  # list of (is_favorable, is_tiebreaker) for compatibility
        self.seats_with_allegiance = seats_with_allegiance or []  # (allegiance_player_id, influence, is_tiebreaker)
        self.player_names = player_names or []
        self.player_color_by_id = player_color_by_id or {}
        self.on_lobby_seat = on_lobby_seat
        self.on_lobby_leader = on_lobby_leader
        self._seat_rects = []
        self._leader_rect = None
        self.setMinimumSize(480, 360)
        self.setFixedHeight(360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor(22, 26, 34))
        self.setPalette(p)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.update()

    def _compute_layout(self):
        """Compute seat positions on an inverted semicircle (∪-shaped arc opening upward). 5 rows of 10 seats, no overlap. Leader centered at bottom."""
        import math
        w, h = max(520, self.width()), max(360, self.height())
        cx = w // 2
        leader_y = h - 35
        self._seat_rects = []
        self._leader_rect = None
        seat_size = 10
        leader_size = 40
        # Arc center at bottom; seats on arcs bulging upward (∪ shape)
        arc_center_y = h - 50
        # 5 rows with radii 120, 100, 80, 60, 40 (back to front) - 20px vertical spacing at center
        radii = [120, 100, 80, 60, 40]
        for row in range(self.NUM_ROWS):
            r = radii[row]
            for col in range(self.SEATS_PER_ROW):
                i = row * self.SEATS_PER_ROW + col
                theta = math.pi * (col + 0.5) / self.SEATS_PER_ROW
                x = cx + r * math.cos(theta) - seat_size / 2
                y = arc_center_y - r * math.sin(theta) - seat_size / 2
                self._seat_rects.append((QRectF(x, y, seat_size, seat_size), False, i))
        self._leader_rect = (
            QRectF(cx - leader_size / 2, leader_y - leader_size / 2, leader_size, leader_size),
            True, 50
        )

    def _color_for_allegiance(self, allegiance_player_id):
        """Player color or light grey for no allegiance."""
        if allegiance_player_id is None:
            return NO_ALLEGIANCE_COLOR
        idx = self.player_color_by_id.get(allegiance_player_id, allegiance_player_id)
        idx = max(0, int(idx)) % len(PLAYER_COLORS)
        return PLAYER_COLORS[idx]

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.seats_data or not isinstance(self.seats_data, (list, tuple)):
            return
        self._compute_layout()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(50, 55, 65), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 6, 6)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for (rect, is_leader, idx), (favorable, _) in zip(self._seat_rects, self.seats_data[:50]):
            if self.seats_with_allegiance and idx < len(self.seats_with_allegiance):
                allegiance, _, _ = self.seats_with_allegiance[idx]
                color = self._color_for_allegiance(allegiance)
            else:
                color = NO_ALLEGIANCE_COLOR if favorable is None else (QColor(34, 139, 34) if favorable else QColor(178, 34, 34))
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(45, 50, 58), 1))
            painter.drawRoundedRect(rect, 5, 5)
        if len(self.seats_data) > 50:
            rect, _, _ = self._leader_rect
            if self.seats_with_allegiance and len(self.seats_with_allegiance) > 50:
                allegiance, _, _ = self.seats_with_allegiance[50]
                color = self._color_for_allegiance(allegiance)
            else:
                favorable = self.seats_data[50][0]
                color = NO_ALLEGIANCE_COLOR if favorable is None else (QColor(34, 139, 34) if favorable else QColor(178, 34, 34))
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(55, 60, 70), 2))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QPen(QColor(220, 220, 220)))
            f = painter.font()
            f.setPointSize(11)
            painter.setFont(f)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "L")

    def _seat_at(self, pos):
        self._compute_layout()
        p = QPointF(pos.x(), pos.y())
        for rect, is_leader, idx in self._seat_rects:
            if rect.contains(p):
                return idx, is_leader
        if self._leader_rect:
            rect, is_leader, idx = self._leader_rect
            if rect.contains(p):
                return idx, is_leader
        return None

    def contextMenuEvent(self, event):
        hit = self._seat_at(event.pos())
        if hit is None:
            return
        seat_idx, is_leader = hit
        menu = QMenu(self)
        # Show allegiance and influence (disabled info line)
        allegiance_str = "No allegiance"
        influence_str = ""
        if self.seats_with_allegiance and seat_idx < len(self.seats_with_allegiance):
            allegiance, influence, _ = self.seats_with_allegiance[seat_idx]
            influence_str = f"  Influence: {influence:.2f}"
            if allegiance is not None and allegiance < len(self.player_names):
                allegiance_str = self.player_names[allegiance]
            elif allegiance is not None:
                allegiance_str = f"Player {allegiance}"
        info_action = menu.addAction(f"Allegiance: {allegiance_str}{influence_str}")
        info_action.setEnabled(False)
        menu.addSeparator()
        action = menu.addAction("Lobby this seat" if not is_leader else "Lobby leader")
        action.triggered.connect(lambda: self._do_lobby(seat_idx, is_leader))
        menu.exec(event.globalPos())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._seat_at(event.pos())
            if hit is not None:
                seat_idx, is_leader = hit
                self._do_lobby(seat_idx, is_leader)
                return
        super().mousePressEvent(event)

    def _do_lobby(self, seat_idx, is_leader):
        if is_leader:
            self.on_lobby_leader()
        else:
            self.on_lobby_seat(seat_idx)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Railroad Baron")
        apply_application_logo(self)
        self.setGeometry(100, 100, 800, 600)
        # Constrain window to never extend beyond screen
        self._screen_geom = None
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if screen:
                self._screen_geom = screen.availableGeometry()
                self.setMaximumSize(self._screen_geom.width(), self._screen_geom.height())
                x, y = 100, 100
                w, h = min(800, self._screen_geom.width()), min(600, self._screen_geom.height())
                x = max(self._screen_geom.x(), min(x, self._screen_geom.x() + self._screen_geom.width() - w))
                y = max(self._screen_geom.y(), min(y, self._screen_geom.y() + self._screen_geom.height() - h))
                self.setGeometry(int(x), int(y), w, h)
        except Exception:
            pass
        central_widget = QWidget()
        central_layout = QVBoxLayout()
        central_widget.setLayout(central_layout)
        self.setCentralWidget(central_widget)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        # Tab close button: visible on dark theme (light grey x); overridden when light theme is on
        self._light_theme = False
        self._tab_close_button_style_dark = (
            "QTabBar::close-button { outline: none; border: none; color: #ffffff; background: transparent; min-width: 16px; min-height: 16px; } "
            "QTabBar::close-button:hover { color: #ffffff; }"
        )
        self._tab_close_button_style_light = (
            "QTabBar::close-button { outline: none; border: none; color: #ffffff; background: transparent; min-width: 16px; min-height: 16px; } "
            "QTabBar::close-button:hover { color: #ffffff; }"
        )
        self._tab_close_button_style = self._tab_close_button_style_dark
        self.tabs.setStyleSheet(self._tab_close_button_style)
        # Apply tab close button style app-wide so all tab bars (including player subtabs) show visible x on dark
        app = QApplication.instance()
        if app is not None and app.styleSheet() in (None, ""):
            app.setStyleSheet(self._tab_close_button_style_dark)
        # Initialize station creation state
        self._station_creation_state = {}
        central_layout.addWidget(self.tabs)
        if self.statusBar():
            self.statusBar().setVisible(False)
        self.status_bar = None
        self.status_bar_layout = None
        self.status_year_label = None
        self.status_money_labels = {}
        self.status_end_year_btn = None
        self.status_end_year_timer_label = None
        self.game_tab = QWidget()
        self.game_layout = QVBoxLayout()
        self.game_tab.setLayout(self.game_layout)
        self.tabs.addTab(self.game_tab, "Game")
        self._remove_game_tab_close_button()
        self.info_tab = None
        self.info_layout = None
        self.marketplace_tab = None
        self.marketplace_layout = None
        self.current_player_index = 0
        self.last_lawyer_replenishment_year = None
        self.layout = self.game_layout
        self.player_tabs_dict = {}
        self._player_tabs_keepalive = []
        self.operations = None
        self.is_network_mode = False
        self.assigned_player_id = 0
        self.network_client = None
        self.network_thread = None
        self.network_session_id = None
        self._local_server_process = None
        self._game_messages = []
        self._message_widgets = []
        self._setup_menu_bar()
        self.SetupGameTab()

    def closeEvent(self, event):
        self._cleanup_network()
        super().closeEvent(event)

    def _refresh_screen_geometry(self):
        """Track available screen area and cap maximum window size."""
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if screen:
                self._screen_geom = screen.availableGeometry()
                self.setMaximumSize(self._screen_geom.width(), self._screen_geom.height())
        except Exception:
            pass

    def _clamp_window_to_screen(self):
        """Keep the window fully inside the available screen area."""
        self._refresh_screen_geometry()
        geom = getattr(self, '_screen_geom', None)
        if not geom:
            return
        if not self.isVisible():
            r = self.geometry()
            new_w = min(r.width(), geom.width())
            new_h = min(r.height(), geom.height())
            new_x = max(geom.x(), min(r.x(), geom.x() + geom.width() - new_w))
            new_y = max(geom.y(), min(r.y(), geom.y() + geom.height() - new_h))
            if (new_w, new_h, new_x, new_y) != (r.width(), r.height(), r.x(), r.y()):
                self.setGeometry(int(new_x), int(new_y), int(new_w), int(new_h))
            return
        fg = self.frameGeometry()
        dx = dy = 0
        shrink_w = shrink_h = 0
        if fg.left() < geom.left():
            dx = geom.left() - fg.left()
        elif fg.right() > geom.right():
            dx = geom.right() - fg.right()
        if fg.top() < geom.top():
            dy = geom.top() - fg.top()
        elif fg.bottom() > geom.bottom():
            dy = geom.bottom() - fg.bottom()
        if fg.width() > geom.width():
            shrink_w = max(shrink_w, fg.width() - geom.width())
        if fg.height() > geom.height():
            shrink_h = max(shrink_h, fg.height() - geom.height())
        if shrink_w or shrink_h:
            self.resize(max(200, self.width() - shrink_w), max(200, self.height() - shrink_h))
            fg = self.frameGeometry()
            if fg.width() > geom.width():
                self.resize(max(200, self.width() - (fg.width() - geom.width())), self.height())
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)
        fg = self.frameGeometry()
        if fg.right() > geom.right():
            self.move(self.x() - (fg.right() - geom.right()), self.y())
        if fg.bottom() > geom.bottom():
            self.move(self.x(), self.y() - (fg.bottom() - geom.bottom()))

    def showEvent(self, event):
        super().showEvent(event)
        self._clamp_window_to_screen()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._clamp_window_to_screen)

    def _attach_game_status_bar(self, central_layout):
        """Build bottom status bar once; player list is a compact vertical column."""
        if self.status_bar is not None:
            return
        self.status_bar = QWidget()
        self.status_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.status_bar_layout = QHBoxLayout()
        self.status_bar_layout.setContentsMargins(4, 2, 4, 2)
        self.status_bar_layout.setSpacing(6)
        self.status_bar.setLayout(self.status_bar_layout)
        self.status_year_label = QLabel("Year: 1830")
        self.status_year_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.status_end_year_btn = QPushButton("End Year")
        self.status_end_year_btn.clicked.connect(self.advance_year_button_clicked)
        self.status_bar_layout.addWidget(self.status_year_label, 0, Qt.AlignmentFlag.AlignTop)

        players_widget = QWidget()
        players_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        players_layout = QVBoxLayout(players_widget)
        players_layout.setContentsMargins(0, 0, 0, 0)
        players_layout.setSpacing(2)
        self.status_money_labels = {}
        for i in range(8):
            row, swatch, text = create_status_player_money_widget()
            self.status_money_labels[i] = {'widget': row, 'swatch': swatch, 'text': text}
            players_layout.addWidget(row)
        self.status_players_widget = players_widget
        self.status_bar_layout.addWidget(players_widget, 0, Qt.AlignmentFlag.AlignTop)

        self.status_end_year_timer_label = QLabel("")
        self.status_end_year_timer_label.setVisible(False)
        self.status_end_year_timer_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.status_bar_layout.addWidget(self.status_end_year_timer_label, 0, Qt.AlignmentFlag.AlignTop)
        self.status_bar_layout.addWidget(self.status_end_year_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.status_save_game_btn = QPushButton("Save Game")
        self.status_save_game_btn.clicked.connect(self.on_save_game_clicked)
        self.status_bar_layout.addWidget(self.status_save_game_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.status_end_game_btn = QPushButton("End Game")
        self.status_end_game_btn.clicked.connect(self.on_end_game_clicked)
        self.status_bar_layout.addWidget(self.status_end_game_btn, 0, Qt.AlignmentFlag.AlignTop)

        messages_widget = QWidget()
        messages_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        messages_layout = QVBoxLayout()
        messages_layout.setContentsMargins(0, 0, 0, 0)
        messages_layout.setSpacing(2)
        messages_widget.setLayout(messages_layout)
        self.status_messages_list = QListWidget()
        self.status_messages_list.setMaximumHeight(40)
        self.status_messages_list.setMaximumWidth(220)
        self.status_messages_list.setMinimumWidth(120)
        for m in self._get_visible_messages():
            self.status_messages_list.insertItem(0, QListWidgetItem(str(m)))
        self._message_widgets.append(self.status_messages_list)
        messages_layout.addWidget(self.status_messages_list)
        chat_row = QHBoxLayout()
        chat_row.setContentsMargins(0, 0, 0, 0)
        chat_row.setSpacing(4)
        self.status_chat_input = QLineEdit()
        self.status_chat_input.setPlaceholderText("Chat...")
        self.status_chat_input.setMaximumWidth(150)
        self.status_chat_input.setFixedHeight(22)
        chat_send_btn = QPushButton("Send")
        chat_send_btn.setFixedHeight(22)
        def _on_chat_send():
            text = self.status_chat_input.text().strip()
            if not text:
                return
            if getattr(self, 'is_network_mode', False) and getattr(self, 'network_client', None) and self.network_client.connected_flag:
                self.network_client.send_chat(text)
                self.status_chat_input.clear()
            elif self.operations:
                cp = self.operations.get_current_player(self.current_player_index)
                name = self.operations.get_player_name(cp) if cp else "Player"
                self.add_message(f"{name}: {text}")
                self.status_chat_input.clear()
        chat_send_btn.clicked.connect(_on_chat_send)
        self.status_chat_input.returnPressed.connect(_on_chat_send)
        chat_row.addWidget(self.status_chat_input)
        chat_row.addWidget(chat_send_btn)
        messages_layout.addLayout(chat_row)
        self.status_bar_layout.addWidget(messages_widget, 0, Qt.AlignmentFlag.AlignTop)
        self.status_bar_layout.addStretch(1)
        central_layout.addWidget(self.status_bar)
    
    def _compact_status_bar_height(self):
        """Shrink the status bar to fit visible player rows and controls."""
        if not getattr(self, 'status_bar', None):
            return
        players_widget = getattr(self, 'status_players_widget', None)
        if players_widget is not None:
            players_widget.adjustSize()
        self.status_bar.adjustSize()
    
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
        self.save_game_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.save_game_action.triggered.connect(self.on_save_game_clicked)
        self.save_game_action.setEnabled(False)  # Enabled when game starts
        file_menu.addAction(self.save_game_action)
        self.addAction(self.save_game_action)
        file_menu.addSeparator()
        self.exit_to_menu_action = QAction("Exit to Main Menu", self)
        self.exit_to_menu_action.triggered.connect(self._exit_to_main_menu)
        file_menu.addAction(self.exit_to_menu_action)
        file_menu.addSeparator()
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
        self.propose_merge_action = QAction("Propose Merge", self)
        self.propose_merge_action.triggered.connect(lambda: self._on_propose_merge_clicked(None))
        self.propose_merge_action.setEnabled(False)
        game_menu.addAction(self.propose_merge_action)
        game_menu.addSeparator()
        self.game_settings_action = QAction("Game Settings...", self)
        self.game_settings_action.triggered.connect(self._show_in_game_settings_dialog)
        self.game_settings_action.setEnabled(False)
        game_menu.addAction(self.game_settings_action)
        game_menu.addSeparator()
        self.game_open_news_log_action = QAction("Open News Log", self)
        self.game_open_news_log_action.setShortcut("Ctrl+Shift+N")
        self.game_open_news_log_action.setEnabled(False)
        self.game_open_news_log_action.triggered.connect(self._on_game_open_news_log)
        game_menu.addAction(self.game_open_news_log_action)
        self.game_open_ai_watch_action = QAction("Open AI Watch", self)
        self.game_open_ai_watch_action.setShortcut("Ctrl+Shift+W")
        self.game_open_ai_watch_action.setEnabled(False)
        self.game_open_ai_watch_action.triggered.connect(self._on_game_open_ai_watch)
        game_menu.addAction(self.game_open_ai_watch_action)

        # View menu - map display and tab visibility
        view_menu = menu_bar.addMenu("View")
        self.view_show_population_action = QAction("Show Population on Map", self)
        self.view_show_population_action.setCheckable(True)
        self.view_show_population_action.setChecked(True)
        self.view_show_population_action.triggered.connect(self._on_view_changed)
        view_menu.addAction(self.view_show_population_action)
        self.view_show_lines_action = QAction("Show Lines on Map", self)
        self.view_show_lines_action.setCheckable(True)
        self.view_show_lines_action.setChecked(True)
        self.view_show_lines_action.triggered.connect(self._on_view_changed)
        view_menu.addAction(self.view_show_lines_action)
        view_menu.addSeparator()
        self.view_show_game_tab_action = QAction("Show Game Tab", self)
        self.view_show_game_tab_action.triggered.connect(lambda _=False: self._open_main_tab_by_title("Game"))
        view_menu.addAction(self.view_show_game_tab_action)
        self.view_show_player_tab_action = QAction("Show Player Tab", self)
        self.view_show_player_tab_action.triggered.connect(self._open_current_player_tab)
        view_menu.addAction(self.view_show_player_tab_action)
        self.view_show_rules_tab_action = QAction("Show Rules Tab", self)
        self.view_show_rules_tab_action.triggered.connect(lambda _=False: self._open_main_tab_by_title("Rules"))
        view_menu.addAction(self.view_show_rules_tab_action)
        self.view_show_vehicles_tab_action = QAction("Show Vehicles Tab", self)
        self.view_show_vehicles_tab_action.triggered.connect(lambda _=False: self._open_main_tab_by_title("Vehicles"))
        view_menu.addAction(self.view_show_vehicles_tab_action)
        self.view_show_ai_watch_tab_action = QAction("Show AI Watch Tab", self)
        self.view_show_ai_watch_tab_action.triggered.connect(lambda _=False: self._open_main_tab_by_title("AI Watch"))
        view_menu.addAction(self.view_show_ai_watch_tab_action)
        self.view_show_news_log_tab_action = QAction("Show News Log Tab", self)
        self.view_show_news_log_tab_action.triggered.connect(lambda _=False: self._open_main_tab_by_title("News Log"))
        view_menu.addAction(self.view_show_news_log_tab_action)
        view_menu.addSeparator()
        self.view_show_other_players_tab_action = QAction("Show Other Players Tab", self)
        self.view_show_other_players_tab_action.triggered.connect(lambda _=False: self._open_main_tab_by_title("Other Players"))
        view_menu.addAction(self.view_show_other_players_tab_action)
        view_menu.addSeparator()
        self.view_light_theme_action = QAction("Light theme", self)
        self.view_light_theme_action.setCheckable(True)
        self.view_light_theme_action.setChecked(False)
        self.view_light_theme_action.triggered.connect(self._apply_theme)
        view_menu.addAction(self.view_light_theme_action)
        # Finance and Government only in Corporation menu (below)
        self.view_show_finance_tab_action = QAction("Show Finance Tab", self)
        self.view_show_finance_tab_action.triggered.connect(lambda _=False: self._open_active_player_subtab("Finance"))
        self.view_show_government_tab_action = QAction("Show Government Tab", self)
        self.view_show_government_tab_action.triggered.connect(lambda _=False: self._open_active_player_subtab("Government"))
        # Corporation menu: reopen all active-player subtabs when closed with x
        def _make_corp_action(label: str, subtab_name: str):
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, s=subtab_name: self._open_active_player_subtab(s))
            return a
        self.view_show_assets_tab_action = _make_corp_action("Show Assets Tab", "Assets")
        self.view_show_law_tab_action = _make_corp_action("Show Law Tab", "Law")
        self.view_show_marketplace_tab_action = _make_corp_action("Show Marketplace Tab", "Marketplace")
        self.view_show_map_tab_action = _make_corp_action("Show Map Tab", "Map")
        self.view_show_construction_tab_action = _make_corp_action("Show Construction Tab", "Construction")
        corporation_menu = menu_bar.addMenu("Corporation")
        corporation_menu.addAction(self.view_show_assets_tab_action)
        corporation_menu.addAction(self.view_show_law_tab_action)
        corporation_menu.addAction(self.view_show_finance_tab_action)
        corporation_menu.addAction(self.view_show_marketplace_tab_action)
        corporation_menu.addAction(self.view_show_map_tab_action)
        corporation_menu.addAction(self.view_show_construction_tab_action)
        corporation_menu.addAction(self.view_show_government_tab_action)
        # Help menu: open Rules and Vehicles tabs
        help_menu = menu_bar.addMenu("Help")
        help_open_rules_action = QAction("Open Rules Tab", self)
        help_open_rules_action.triggered.connect(self._help_open_rules_tab)
        help_menu.addAction(help_open_rules_action)
        help_open_vehicles_action = QAction("Open Vehicles Tab", self)
        help_open_vehicles_action.triggered.connect(self._help_open_vehicles_tab)
        help_menu.addAction(help_open_vehicles_action)
    
    def _apply_theme(self):
        """Apply light or dark theme; update tab close button style so the x is visible."""
        use_light = getattr(self, 'view_light_theme_action', None) and self.view_light_theme_action.isChecked()
        self._light_theme = bool(use_light)
        app = QApplication.instance()
        if app is None:
            return
        if use_light:
            self._tab_close_button_style = self._tab_close_button_style_light
            light_css = (
                "QMainWindow, QWidget, QDialog { background-color: #f5f5f5; color: #1a1a1a; } "
                "QTabWidget::pane { background-color: #ffffff; border: 1px solid #d0d0d0; } "
                "QTabBar::tab { background-color: #e0e0e0; color: #1a1a1a; padding: 6px 12px; } "
                "QTabBar::tab:selected { background-color: #ffffff; } "
                "QGroupBox { background-color: transparent; color: #1a1a1a; } "
                "QLabel { color: #1a1a1a; } "
                "QPushButton { background-color: #e0e0e0; color: #1a1a1a; border: 1px solid #b0b0b0; } "
                "QPushButton:hover { background-color: #d0d0d0; } "
                "QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background-color: #ffffff; color: #1a1a1a; border: 1px solid #b0b0b0; } "
                "QTextEdit, QTextBrowser, QPlainTextEdit { background-color: #ffffff; color: #1a1a1a; } "
                "QScrollArea { background-color: #f5f5f5; } "
                "QMenuBar { background-color: #e8e8e8; color: #1a1a1a; } "
                "QMenuBar::item:selected { background-color: #c0c0c0; } "
                "QMenu { background-color: #ffffff; color: #1a1a1a; } "
                "QMenu::item:selected { background-color: #c0c0c0; } "
                "QCheckBox { color: #1a1a1a; } "
                "QSlider::groove:horizontal { background: #d0d0d0; } "
                "QSlider::handle:horizontal { background: #606060; } "
            )
            app.setStyleSheet(light_css + self._tab_close_button_style_light)
        else:
            self._tab_close_button_style = self._tab_close_button_style_dark
            app.setStyleSheet(self._tab_close_button_style_dark)
        if hasattr(self, 'tabs') and self.tabs is not None:
            self.tabs.setStyleSheet(self._tab_close_button_style)
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                if isinstance(w, QTabWidget):
                    w.setStyleSheet(self._tab_close_button_style)

    def _help_open_rules_tab(self):
        """Help menu: show Rules tab and switch to it."""
        self._open_main_tab_by_title("Rules")

    def _help_open_vehicles_tab(self):
        """Help menu: show Vehicles tab and switch to it."""
        self._open_main_tab_by_title("Vehicles")

    def _menu_advance_year(self):
        """Menu action: Advance year (same as End Year button)"""
        if self.operations:
            self.advance_year_button_clicked()
    
    def _menu_refresh_displays(self):
        """Menu action: Refresh all displays"""
        if self.operations:
            self.refresh_all_displays()

    def _on_view_changed(self):
        """View menu: map display options changed - refresh map."""
        if self.operations and hasattr(self, 'DisplayMapInfo'):
            self.DisplayMapInfo()

    def _on_view_tab_changed(self):
        """Backward-compatibility shim; tab actions now open/focus directly."""
        return

    def _remove_game_tab_close_button(self):
        """Remove the close button from the Game tab (index 0) so it cannot be closed."""
        if not hasattr(self, 'tabs') or self.tabs is None or self.tabs.count() == 0:
            return
        if self.tabs.tabText(0) != "Game":
            return
        try:
            tab_bar = self.tabs.tabBar()
            btn = tab_bar.tabButton(0, QTabBar.ButtonPosition.RightSide)
            if btn is not None:
                btn.deleteLater()
                tab_bar.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        except Exception:
            pass

    def _get_assigned_player_id(self):
        """Player id for the local user's tab (network assigned player or current player)."""
        return getattr(self, '_our_player_id', None) or getattr(self, 'current_player_index', None)

    def _get_assigned_player_tab_widget(self):
        player_id = self._get_assigned_player_id()
        if player_id is None:
            return None
        d = getattr(self, 'player_tabs_dict', {}).get(player_id)
        return d.get('player_tabs') if d else None

    def _is_assigned_player_main_tab(self, index: int) -> bool:
        widget = self._get_assigned_player_tab_widget()
        if widget is None or not hasattr(self, 'tabs'):
            return False
        return self.tabs.widget(index) is widget

    def _remove_assigned_player_tab_close_button(self):
        """Remove close button from the local user's player tab so it cannot be closed."""
        if not hasattr(self, 'tabs') or self.tabs is None:
            return
        widget = self._get_assigned_player_tab_widget()
        if widget is None:
            return
        try:
            tab_bar = self.tabs.tabBar()
            for i in range(self.tabs.count()):
                if self.tabs.widget(i) is widget:
                    btn = tab_bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
                    if btn is not None:
                        btn.deleteLater()
                        tab_bar.setTabButton(i, QTabBar.ButtonPosition.RightSide, None)
                    break
        except Exception:
            pass

    def _player_subtab_text_to_action(self):
        """Map player subtab text to its View/Corporation menu action (for close -> uncheck)."""
        return {
            "Assets": getattr(self, "view_show_assets_tab_action", None),
            "Law": getattr(self, "view_show_law_tab_action", None),
            "Finance": getattr(self, "view_show_finance_tab_action", None),
            "Marketplace": getattr(self, "view_show_marketplace_tab_action", None),
            "Map": getattr(self, "view_show_map_tab_action", None),
            "Construction": getattr(self, "view_show_construction_tab_action", None),
            "Government": getattr(self, "view_show_government_tab_action", None),
        }

    def _on_player_subtab_close_requested(self, player_tab_widget, index: int):
        """Handle player subtab close: hide tab; menu actions reopen it on click."""
        if player_tab_widget is None or index < 0 or index >= player_tab_widget.count():
            return
        tab_text = player_tab_widget.tabText(index)
        try:
            player_tab_widget.setTabVisible(index, False)
        except Exception:
            pass
        # No checkbox state to maintain; actions are one-shot open/focus.

    def _on_tab_close_requested(self, index: int):
        """Handle tab close button: hide tab; menu actions can reopen/focus it."""
        if not hasattr(self, 'tabs') or index < 0 or index >= self.tabs.count():
            return
        tab_text = self.tabs.tabText(index)
        if tab_text == "Game":
            return  # Game tab cannot be closed
        if self._is_assigned_player_main_tab(index):
            return  # Local user's player tab cannot be closed
        try:
            self.tabs.setTabVisible(index, False)
        except Exception:
            pass
        # Player-name tabs stay hidden until user switches tab or re-adds

    def _apply_view_tab_visibility(self):
        """Deprecated with action-style tab reopening; kept as a no-op for callers."""
        return

    def changeEvent(self, event):
        """Keep window within screen bounds when window state changes."""
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self._refresh_screen_geometry()
            if not (self.windowState() & Qt.WindowState.WindowMaximized):
                self.setMinimumSize(0, 0)
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._clamp_window_to_screen)

    def resizeEvent(self, event):
        """Refresh elided status labels and keep Leaflet sized to the map view."""
        super().resizeEvent(event)
        self._invalidate_all_map_web_views()
        if self.operations and getattr(self, 'status_money_labels', None):
            try:
                self.update_status_bar()
            except Exception:
                pass

    def _update_propose_merge_action(self):
        """Update Propose Merge menu action and Law tab buttons enabled state. Propose Merge is available at any time when there is at least one merge target."""
        if hasattr(self, 'propose_merge_action'):
            try:
                enabled = False
                if self.operations and getattr(self.operations.game, 'merge_enabled', False):
                    cp = self.operations.get_current_player(self.current_player_index)
                    if cp and not getattr(cp, 'is_ai', False) and not self.operations.is_player_eliminated(cp):
                        effective = self.operations.get_effective_player(cp)
                        if effective:
                            targets = self.operations.get_merge_targets(effective)
                            enabled = len(targets) > 0
                self.propose_merge_action.setEnabled(enabled)
            except RuntimeError:
                pass
        # Update each player tab's Propose Merge button (Law tab) - guard against deleted widgets
        if hasattr(self, 'player_tabs_dict') and self.operations and getattr(self.operations.game, 'merge_enabled', False):
            for pid, d in list(self.player_tabs_dict.items()):
                if not isinstance(d, dict):
                    continue
                btn = d.get('propose_merge_btn')
                if btn is None:
                    continue
                try:
                    p = next((pl for pl in self.operations.get_players() if self.operations.get_player_id(pl) == pid), None)
                    if p is None or getattr(p, 'is_ai', False) or self.operations.is_player_eliminated(p):
                        btn.setEnabled(False)
                        continue
                    effective = self.operations.get_effective_player(p)
                    if not effective:
                        btn.setEnabled(False)
                        continue
                    targets = self.operations.get_merge_targets(effective)
                    btn.setEnabled(len(targets) > 0)
                except RuntimeError:
                    pass

    def _on_propose_merge_clicked(self, player=None):
        """Handle Propose Merge - show dialog to pick a company (AI or human) to merge with. Available at any time from the Law tab."""
        if not self.operations or not getattr(self.operations.game, 'merge_enabled', False):
            return
        cp = player if player is not None else self.operations.get_current_player(self.current_player_index)
        if not cp or getattr(cp, 'is_ai', False) or self.operations.is_player_eliminated(cp):
            return
        effective = self.operations.get_effective_player(cp)
        if not effective:
            return
        targets = self.operations.get_merge_targets(effective)
        if not targets:
            QMessageBox.information(self, "Propose Merge", "No other companies are available to merge with.")
            return
        def label(p):
            name = self.operations.get_player_name(p)
            suffix = " (AI)" if getattr(p, 'is_ai', False) else ""
            if self.operations.is_player_desperate(p):
                suffix += " — desperate, will accept"
            return f"{name}{suffix}"
        if len(targets) == 1:
            other = targets[0]
            reply = QMessageBox.question(
                self, "Propose Merge",
                f"Propose merger with {label(other)}? You will acquire all their assets."
                + (" They are desperate and will accept." if self.operations.is_player_desperate(other) else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                result = self.operations.merge_players(effective, other)
                if result.get('success'):
                    self.add_message(f"You merged with {result['target_name']}. All their assets are now yours.")
                    QMessageBox.information(self, "Merger Complete", f"You have merged with {result['target_name']}. All their assets are now yours.")
                    self.refresh_all_displays()
        else:
            names = [label(p) for p in targets]
            name, ok = QInputDialog.getItem(
                self, "Propose Merge", "Select company to merge with:",
                names, 0, False
            )
            if ok and name:
                other = next(p for p in targets if label(p) == name)
                result = self.operations.merge_players(effective, other)
                if result.get('success'):
                    self.add_message(f"You merged with {result['target_name']}. All their assets are now yours.")
                    QMessageBox.information(self, "Merger Complete", f"You have merged with {result['target_name']}. All their assets are now yours.")
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
        
        if getattr(self, 'is_network_mode', False):
            self._cleanup_network()
        
        self.operations = None
        from inflation_utils import clear_inflation_rate_multiplier
        clear_inflation_rate_multiplier()
        while self.tabs.count() > 1:
            self.tabs.removeTab(1)
        self.player_tabs_dict = {}
        self._player_tabs_keepalive = []
        self.current_player_index = 0
        if self.status_bar:
            if hasattr(self.status_bar, 'clearMessage'):
                self.status_bar.clearMessage()
            if self.status_bar_layout:
                status_messages = getattr(self, 'status_messages_list', None)
                if status_messages and status_messages in self._message_widgets:
                    self._message_widgets.remove(status_messages)
                while self.status_bar_layout.count():
                    item = self.status_bar_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
        
        self.status_year_label = None
        self.status_money_labels = {}
        self.status_end_year_btn = None
        self.status_end_year_timer_label = None
        self.tabs.setCurrentIndex(0)
        self.last_lawyer_replenishment_year = None
        self._station_creation_state = {}
        self.advance_year_action.setEnabled(False)
        self.refresh_displays_action.setEnabled(False)
        if hasattr(self, 'game_settings_action'):
            self.game_settings_action.setEnabled(False)
        if hasattr(self, 'game_open_news_log_action'):
            self.game_open_news_log_action.setEnabled(False)
        if hasattr(self, 'game_open_ai_watch_action'):
            self.game_open_ai_watch_action.setEnabled(False)

    def _cleanup_network(self):
        """Disconnect client, stop network thread, and stop local server if running."""
        if getattr(self, 'network_client', None):
            try:
                self.network_client.disconnect_from_server()
            except Exception:
                pass
            self.network_client = None
        network_thread = getattr(self, 'network_thread', None)
        if network_thread is not None:
            try:
                if network_thread.isRunning():
                    network_thread.wait(2000)
            except Exception:
                pass
            self.network_thread = None
        self.network_session_id = None
        server_proc = getattr(self, '_local_server_process', None)
        if server_proc is not None:
            try:
                if server_proc.poll() is None:
                    server_proc.terminate()
                    try:
                        server_proc.wait(timeout=3)
                    except Exception:
                        server_proc.kill()
            except Exception:
                pass
            self._local_server_process = None

    def _exit_to_main_menu(self, skip_confirm=False):
        """Leave current session and return to the initial game mode selection screen."""
        if not skip_confirm and self.operations is not None:
            reply = QMessageBox.question(
                self,
                "Exit to Main Menu",
                "Leave the current game and return to the main menu?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._cleanup_network()
        self.hide()
        dialog = GameModeDialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.choice == 'local':
                new_window = MainWindow()
            elif dialog.choice == 'network':
                new_window = NetworkedMainWindow(network_mode=True)
            else:
                self.close()
                QApplication.instance().quit()
                return
            new_window.show()
            new_window.raise_()
            new_window.activateWindow()
        else:
            QApplication.instance().quit()
        self.close()

    def SetupGameTab(self):
        from PyQt6.QtWidgets import QLineEdit
        from map_preset_loader import list_available_presets, get_map_display_name
        from game_presets import (
            MARKETPLACE_YEARS_VISIBLE,
            LINE_BUILD_SPEED_MULTIPLIER,
            CONSTRUCTION_COST_MULTIPLIER,
            MARKETPLACE_BULK_DISCOUNT_PERCENT,
            MARKETPLACE_BULK_DISCOUNT_THRESHOLD,
            MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR,
            AI_MERGE_MIN_TURNS_TO_LIVE_FOR_ACCEPT,
            AI_MERGE_MIN_YEARS_BEFORE_CONSIDER,
            AI_MERGE_AUTO_FAIL_FIRST_N_YEARS,
            AI_MERGE_HEALTHY_PROFIT_THRESHOLD_PCT,
            AI_MERGE_REJECT_CHANCE_PCT,
            WAR_START_CHANCE_PER_YEAR,
            WAR_END_CHANCE_PER_YEAR,
            WAR_WORKER_DRAFT_CHANCE_PER_YEAR,
            WAR_NEW_WORKER_COST_MULTIPLIER,
        )
        settings_tabs = QTabWidget()
        # --- Map Settings tab ---
        map_settings_tab = QWidget()
        map_settings_layout = QVBoxLayout()
        map_settings_tab.setLayout(map_settings_layout)
        map_settings_layout.addWidget(QLabel("Map Settings"))
        map_type_layout = QHBoxLayout()
        map_type_layout.addWidget(QLabel("Map Type:"))
        self.map_type_combo = QComboBox()
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
        default_map_index = 0
        for _mi, _pn in enumerate(self._map_preset_names):
            if _pn == "nyc":
                default_map_index = _mi
                break
        self.map_type_combo.setCurrentIndex(default_map_index)
        map_type_layout.addWidget(self.map_type_combo)
        map_settings_layout.addLayout(map_type_layout)
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
        map_settings_layout.addLayout(map_size_layout)
        settings_tabs.addTab(_wrap_settings_tab_scroll(map_settings_tab), "Map")
        # --- AI Settings tab ---
        ai_settings_tab = QWidget()
        ai_settings_layout = QVBoxLayout()
        ai_settings_tab.setLayout(ai_settings_layout)
        ai_settings_layout.addWidget(QLabel("AI Settings"))
        self.ai_watch_checkbox = QCheckBox("Enable AI Watch tab (shows AI actions during End Year)")
        self.ai_watch_checkbox.setChecked(False)
        self.ai_watch_checkbox.setToolTip("Only available when the game has AI players. Must be set before starting.")
        ai_settings_layout.addWidget(self.ai_watch_checkbox)
        self.ai_debug_notifications_checkbox = QCheckBox("AI debug notifications (report AI logic bugs)")
        self.ai_debug_notifications_checkbox.setChecked(True)
        self.ai_debug_notifications_checkbox.setToolTip(
            "When enabled, shows warnings when the AI fails to expand despite surplus cash, "
            "street build failures, idle turns, and similar issues."
        )
        ai_settings_layout.addWidget(self.ai_debug_notifications_checkbox)
        ai_min_pop_layout = QHBoxLayout()
        ai_min_pop_layout.addWidget(QLabel("AI min population for build:"))
        self.ai_min_population_spin = QSpinBox()
        self.ai_min_population_spin.setMinimum(0)
        self.ai_min_population_spin.setMaximum(100000)
        self.ai_min_population_spin.setValue(100)
        self.ai_min_population_spin.setToolTip("AI will only build lines connecting population points with at least this population (default 100)")
        ai_min_pop_layout.addWidget(self.ai_min_population_spin)
        ai_settings_layout.addLayout(ai_min_pop_layout)
        ai_settings_layout.addWidget(QLabel("Random AI spawn:"))
        self.random_ai_spawn_checkbox = QCheckBox("Random AI spawn (new AI joins periodically)")
        self.random_ai_spawn_checkbox.setChecked(True)
        self.random_ai_spawn_checkbox.setToolTip("When enabled, a new AI player joins the game at random intervals.")
        ai_settings_layout.addWidget(self.random_ai_spawn_checkbox)
        ai_spawn_interval_layout = QHBoxLayout()
        ai_spawn_interval_layout.addWidget(QLabel("Average spawn interval (years):"))
        self.random_ai_spawn_interval_spin = QSpinBox()
        self.random_ai_spawn_interval_spin.setMinimum(5)
        self.random_ai_spawn_interval_spin.setMaximum(200)
        self.random_ai_spawn_interval_spin.setValue(30)
        self.random_ai_spawn_interval_spin.setToolTip("Average years between new AI joining (default 30)")
        ai_spawn_interval_layout.addWidget(self.random_ai_spawn_interval_spin)
        ai_settings_layout.addLayout(ai_spawn_interval_layout)
        settings_tabs.addTab(_wrap_settings_tab_scroll(ai_settings_tab), "AI")
        # --- Player Settings tab ---
        player_settings_tab = QWidget()
        player_settings_layout = QVBoxLayout()
        player_settings_tab.setLayout(player_settings_layout)
        player_settings_layout.addWidget(QLabel("Player Settings"))
        player_settings_layout.addWidget(QLabel("Number of players:"))
        self.player_count_spin = QSpinBox()
        self.player_count_spin.setMinimum(1)
        self.player_count_spin.setMaximum(8)
        self.player_count_spin.setValue(4)
        self.player_count_spin.valueChanged.connect(self.rebuild_player_name_fields)
        player_settings_layout.addWidget(self.player_count_spin)
        human_count_layout = QHBoxLayout()
        human_count_layout.addWidget(QLabel("Number of human players:"))
        self.human_count_spin = QSpinBox()
        self.human_count_spin.setMinimum(1)
        self.human_count_spin.setMaximum(8)
        self.human_count_spin.setValue(1)
        human_count_layout.addWidget(self.human_count_spin)
        human_count_layout.addWidget(QLabel("(AI players = total - human)"))
        player_settings_layout.addLayout(human_count_layout)
        self.human_count_spin.valueChanged.connect(lambda v: self.rebuild_player_name_fields(self.player_count_spin.value()))
        starting_money_layout = QHBoxLayout()
        starting_money_layout.addWidget(QLabel("Starting Money per Player: $"))
        self.starting_money_spin = QSpinBox()
        self.starting_money_spin.setMinimum(1000)
        self.starting_money_spin.setMaximum(1000000)
        self.starting_money_spin.setValue(STARTING_MONEY)
        self.starting_money_spin.setSingleStep(1000)
        starting_money_layout.addWidget(self.starting_money_spin)
        player_settings_layout.addLayout(starting_money_layout)
        set_all_difficulty_layout = QHBoxLayout()
        set_all_difficulty_layout.addWidget(QLabel("Set all AI difficulty:"))
        self.set_all_ai_difficulty_combo = QComboBox()
        self.set_all_ai_difficulty_combo.addItems(["Easy", "Medium", "Hard"])
        self.set_all_ai_difficulty_combo.setCurrentIndex(2)  # Hard
        self.set_all_ai_difficulty_combo.setToolTip(
            "Applies to every AI player below and sets the default for random AI spawns."
        )
        self.set_all_ai_difficulty_combo.currentIndexChanged.connect(self._on_set_all_ai_difficulty_changed)
        set_all_difficulty_layout.addWidget(self.set_all_ai_difficulty_combo)
        set_all_difficulty_layout.addStretch()
        player_settings_layout.addLayout(set_all_difficulty_layout)
        player_settings_layout.addWidget(QLabel("Player names and AI settings (focus/difficulty for each AI):"))
        self.names_container = QVBoxLayout()
        self.name_edits = []
        self.ai_focus_combos = []
        self.ai_difficulty_combos = []
        self.rebuild_player_name_fields(self.player_count_spin.value())
        player_settings_layout.addLayout(self.names_container)
        settings_tabs.addTab(_wrap_settings_tab_scroll(player_settings_tab), "Players")
        # --- Game Settings tab ---
        game_settings_tab = QWidget()
        game_settings_layout = QVBoxLayout()
        game_settings_tab.setLayout(game_settings_layout)
        game_settings_layout.addWidget(QLabel("Game Settings"))
        from ruleset_loader import (
            DEFAULT_RULESET_NAME,
            ensure_rulesets_layout,
            list_rulesets,
            set_active_ruleset,
        )
        ensure_rulesets_layout()
        set_active_ruleset(DEFAULT_RULESET_NAME)
        ruleset_layout = QHBoxLayout()
        ruleset_layout.addWidget(QLabel("Ruleset:"))
        self.game_ruleset_combo = QComboBox()
        for rs_name in list_rulesets():
            self.game_ruleset_combo.addItem(rs_name)
        default_idx = self.game_ruleset_combo.findText(DEFAULT_RULESET_NAME)
        if default_idx >= 0:
            self.game_ruleset_combo.setCurrentIndex(default_idx)
        self.game_ruleset_combo.setToolTip("Rules and vehicles for new games. Duplicate default to customize.")
        self.game_ruleset_combo.currentTextChanged.connect(self._on_game_settings_ruleset_changed)
        ruleset_layout.addWidget(self.game_ruleset_combo, 1)
        manage_ruleset_btn = QPushButton("Manage Rulesets…")
        manage_ruleset_btn.clicked.connect(self._open_ruleset_editor_from_settings)
        ruleset_layout.addWidget(manage_ruleset_btn)
        game_settings_layout.addLayout(ruleset_layout)
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
        game_settings_layout.addLayout(replenishment_layout)
        disaster_frequency_layout = QHBoxLayout()
        disaster_frequency_layout.addWidget(QLabel("Natural Disaster Frequency:"))
        self.disaster_frequency_spin = QDoubleSpinBox()
        self.disaster_frequency_spin.setMinimum(0.1)
        self.disaster_frequency_spin.setMaximum(5.0)
        self.disaster_frequency_spin.setValue(1.0)
        self.disaster_frequency_spin.setSingleStep(0.1)
        self.disaster_frequency_spin.setDecimals(1)
        disaster_frequency_layout.addWidget(self.disaster_frequency_spin)
        disaster_frequency_layout.addWidget(QLabel("(1.0 = normal, 2.0 = twice as often, 0.5 = half)"))
        game_settings_layout.addLayout(disaster_frequency_layout)
        game_settings_layout.addWidget(QLabel("War (random world events; checked each year at End Year):"))
        war_start_layout = QHBoxLayout()
        war_start_layout.addWidget(QLabel("Start chance per year (%):"))
        self.war_start_chance_spin = QSpinBox()
        self.war_start_chance_spin.setRange(0, 100)
        self.war_start_chance_spin.setValue(int(round(WAR_START_CHANCE_PER_YEAR * 100)))
        war_start_layout.addWidget(self.war_start_chance_spin)
        war_start_layout.addStretch()
        game_settings_layout.addLayout(war_start_layout)
        war_end_layout = QHBoxLayout()
        war_end_layout.addWidget(QLabel("End chance per year while at war (%):"))
        self.war_end_chance_spin = QSpinBox()
        self.war_end_chance_spin.setRange(0, 100)
        self.war_end_chance_spin.setValue(int(round(WAR_END_CHANCE_PER_YEAR * 100)))
        war_end_layout.addWidget(self.war_end_chance_spin)
        war_end_layout.addStretch()
        game_settings_layout.addLayout(war_end_layout)
        war_draft_layout = QHBoxLayout()
        war_draft_layout.addWidget(QLabel("Worker draft event chance per war year (%):"))
        self.war_draft_chance_spin = QSpinBox()
        self.war_draft_chance_spin.setRange(0, 100)
        self.war_draft_chance_spin.setValue(int(round(WAR_WORKER_DRAFT_CHANCE_PER_YEAR * 100)))
        war_draft_layout.addWidget(self.war_draft_chance_spin)
        war_draft_layout.addStretch()
        game_settings_layout.addLayout(war_draft_layout)
        war_cost_layout = QHBoxLayout()
        war_cost_layout.addWidget(QLabel("New worker hire cost multiplier during war:"))
        self.war_worker_cost_multiplier_spin = QDoubleSpinBox()
        self.war_worker_cost_multiplier_spin.setRange(1.0, 3.0)
        self.war_worker_cost_multiplier_spin.setSingleStep(0.05)
        self.war_worker_cost_multiplier_spin.setDecimals(2)
        self.war_worker_cost_multiplier_spin.setValue(WAR_NEW_WORKER_COST_MULTIPLIER)
        war_cost_layout.addWidget(self.war_worker_cost_multiplier_spin)
        war_cost_layout.addStretch()
        game_settings_layout.addLayout(war_cost_layout)
        line_speed_layout = QHBoxLayout()
        line_speed_layout.addWidget(QLabel("Line Build Speed:"))
        self.line_build_speed_spin = QDoubleSpinBox()
        self.line_build_speed_spin.setMinimum(0.5)
        self.line_build_speed_spin.setMaximum(5.0)
        self.line_build_speed_spin.setValue(LINE_BUILD_SPEED_MULTIPLIER)
        self.line_build_speed_spin.setSingleStep(0.1)
        self.line_build_speed_spin.setDecimals(1)
        line_speed_layout.addWidget(self.line_build_speed_spin)
        line_speed_layout.addWidget(QLabel("(2.0 = lines build in half the time)"))
        game_settings_layout.addLayout(line_speed_layout)
        construction_cost_layout = QHBoxLayout()
        construction_cost_layout.addWidget(QLabel("Construction cost multiplier:"))
        self.construction_cost_multiplier_spin = QDoubleSpinBox()
        self.construction_cost_multiplier_spin.setMinimum(0.5)
        self.construction_cost_multiplier_spin.setMaximum(10.0)
        self.construction_cost_multiplier_spin.setValue(CONSTRUCTION_COST_MULTIPLIER)
        self.construction_cost_multiplier_spin.setSingleStep(0.5)
        self.construction_cost_multiplier_spin.setDecimals(1)
        self.construction_cost_multiplier_spin.setToolTip(
            "Multiplier for all construction (lines, extensions, stations, upgrades, ferries). Default 2.0 = double cost."
        )
        construction_cost_layout.addWidget(self.construction_cost_multiplier_spin)
        game_settings_layout.addLayout(construction_cost_layout)
        profit_margin_layout = QHBoxLayout()
        profit_margin_layout.addWidget(QLabel("Profit margin (line revenue multiplier):"))
        self.profit_margin_spin = QDoubleSpinBox()
        self.profit_margin_spin.setMinimum(0.25)
        self.profit_margin_spin.setMaximum(5.0)
        self.profit_margin_spin.setValue(1.0)
        self.profit_margin_spin.setSingleStep(0.25)
        self.profit_margin_spin.setDecimals(2)
        self.profit_margin_spin.setToolTip("Multiplier applied to line revenue (1.0 = default). Lower = harder economy.")
        profit_margin_layout.addWidget(self.profit_margin_spin)
        game_settings_layout.addLayout(profit_margin_layout)
        game_settings_layout.addWidget(QLabel("Mergers:"))
        self.merge_enabled_checkbox = QCheckBox("Allow mergers (master switch)")
        self.merge_enabled_checkbox.setChecked(True)
        self.merge_enabled_checkbox.setToolTip("When unchecked, no mergers are allowed.")
        game_settings_layout.addWidget(self.merge_enabled_checkbox)
        self.merge_human_human_checkbox = QCheckBox("Allow mergers between humans")
        self.merge_human_human_checkbox.setChecked(True)
        self.merge_human_human_checkbox.setToolTip("When enabled, two human players can merge; both then control the merged company from their own tab/device.")
        game_settings_layout.addWidget(self.merge_human_human_checkbox)
        self.merge_ai_ai_checkbox = QCheckBox("Allow mergers between AIs")
        self.merge_ai_ai_checkbox.setChecked(True)
        self.merge_ai_ai_checkbox.setToolTip("When enabled, desperate AIs can merge with other AIs (e.g. doomed-merge).")
        game_settings_layout.addWidget(self.merge_ai_ai_checkbox)
        self.merge_ai_human_checkbox = QCheckBox("Allow mergers between humans and AIs")
        self.merge_ai_human_checkbox.setChecked(True)
        self.merge_ai_human_checkbox.setToolTip("When enabled, humans can propose merger with desperate AIs (and vice versa).")
        game_settings_layout.addWidget(self.merge_ai_human_checkbox)
        merge_healthy_layout = QHBoxLayout()
        merge_healthy_layout.addWidget(QLabel("AI healthy profit threshold %:"))
        self.merge_ai_healthy_threshold_spin = QDoubleSpinBox()
        self.merge_ai_healthy_threshold_spin.setMinimum(0)
        self.merge_ai_healthy_threshold_spin.setMaximum(100)
        self.merge_ai_healthy_threshold_spin.setValue(float(AI_MERGE_HEALTHY_PROFIT_THRESHOLD_PCT))
        self.merge_ai_healthy_threshold_spin.setSuffix("%")
        self.merge_ai_healthy_threshold_spin.setToolTip("If AI is not losing more than this % of its profit this year, it auto-rejects merger.")
        merge_healthy_layout.addWidget(self.merge_ai_healthy_threshold_spin)
        game_settings_layout.addLayout(merge_healthy_layout)
        merge_reject_layout = QHBoxLayout()
        merge_reject_layout.addWidget(QLabel("AI reject merger chance %:"))
        self.merge_ai_reject_chance_spin = QDoubleSpinBox()
        self.merge_ai_reject_chance_spin.setMinimum(0)
        self.merge_ai_reject_chance_spin.setMaximum(100)
        self.merge_ai_reject_chance_spin.setValue(float(AI_MERGE_REJECT_CHANCE_PCT))
        self.merge_ai_reject_chance_spin.setSuffix("%")
        self.merge_ai_reject_chance_spin.setToolTip("When not healthy, AI rejects merger with this probability (e.g. 90% = reject 9 out of 10).")
        merge_reject_layout.addWidget(self.merge_ai_reject_chance_spin)
        game_settings_layout.addLayout(merge_reject_layout)
        merge_ttl_layout = QHBoxLayout()
        merge_ttl_layout.addWidget(QLabel("AI auto-reject merge if turns to live >"))
        self.merge_ai_min_turns_to_live_spin = QSpinBox()
        self.merge_ai_min_turns_to_live_spin.setMinimum(0)
        self.merge_ai_min_turns_to_live_spin.setMaximum(200)
        self.merge_ai_min_turns_to_live_spin.setValue(int(AI_MERGE_MIN_TURNS_TO_LIVE_FOR_ACCEPT))
        self.merge_ai_min_turns_to_live_spin.setToolTip(
            "AI rejects merger offers when projected survival at current loss rate exceeds this many turns."
        )
        merge_ttl_layout.addWidget(self.merge_ai_min_turns_to_live_spin)
        merge_ttl_layout.addWidget(QLabel("turns"))
        merge_ttl_layout.addStretch()
        game_settings_layout.addLayout(merge_ttl_layout)
        merge_early_layout = QHBoxLayout()
        merge_early_layout.addWidget(QLabel("AI years before considering merge:"))
        self.merge_ai_min_years_before_consider_spin = QSpinBox()
        self.merge_ai_min_years_before_consider_spin.setMinimum(0)
        self.merge_ai_min_years_before_consider_spin.setMaximum(50)
        self.merge_ai_min_years_before_consider_spin.setValue(int(AI_MERGE_MIN_YEARS_BEFORE_CONSIDER))
        self.merge_ai_min_years_before_consider_spin.setToolTip(
            "Calendar years since game start before AI runs merge logic (proposals, AI–AI merges, desperate merge)."
        )
        merge_early_layout.addWidget(self.merge_ai_min_years_before_consider_spin)
        merge_early_layout.addWidget(QLabel("Auto-fail AI merges first N years:"))
        self.merge_ai_auto_fail_first_n_years_spin = QSpinBox()
        self.merge_ai_auto_fail_first_n_years_spin.setMinimum(0)
        self.merge_ai_auto_fail_first_n_years_spin.setMaximum(50)
        self.merge_ai_auto_fail_first_n_years_spin.setValue(int(AI_MERGE_AUTO_FAIL_FIRST_N_YEARS))
        self.merge_ai_auto_fail_first_n_years_spin.setToolTip(
            "While elapsed years since start are below this, any merger involving an AI is rejected."
        )
        merge_early_layout.addWidget(self.merge_ai_auto_fail_first_n_years_spin)
        merge_early_layout.addStretch()
        game_settings_layout.addLayout(merge_early_layout)
        inflation_layout = QHBoxLayout()
        inflation_layout.addWidget(QLabel("Inflation Rate:"))
        self.inflation_rate_spin = QDoubleSpinBox()
        self.inflation_rate_spin.setMinimum(0.25)
        self.inflation_rate_spin.setMaximum(3.0)
        self.inflation_rate_spin.setValue(1.0)
        self.inflation_rate_spin.setSingleStep(0.25)
        self.inflation_rate_spin.setDecimals(2)
        self.inflation_rate_spin.setToolTip("1.0 = default (10x by 2050). 0.5 = half inflation, 2.0 = double.")
        inflation_layout.addWidget(self.inflation_rate_spin)
        inflation_layout.addWidget(QLabel("(1.0 = default, 0.5 = half, 2.0 = double)"))
        game_settings_layout.addLayout(inflation_layout)
        settings_tabs.addTab(_wrap_settings_tab_scroll(game_settings_tab), "Game")
        game_tab_index = settings_tabs.indexOf(game_settings_tab)
        if game_tab_index > 0:
            settings_tabs.tabBar().moveTab(game_tab_index, 0)
        # --- Marketplace Settings tab ---
        marketplace_settings_tab = QWidget()
        marketplace_settings_layout = QVBoxLayout()
        marketplace_settings_tab.setLayout(marketplace_settings_layout)
        marketplace_settings_layout.addWidget(QLabel("Marketplace Settings"))
        marketplace_years_layout = QHBoxLayout()
        marketplace_years_layout.addWidget(QLabel("Marketplace Years Visible:"))
        self.marketplace_years_spin = QSpinBox()
        self.marketplace_years_spin.setMinimum(1)
        self.marketplace_years_spin.setMaximum(200)
        self.marketplace_years_spin.setValue(MARKETPLACE_YEARS_VISIBLE)
        marketplace_years_layout.addWidget(self.marketplace_years_spin)
        marketplace_years_layout.addWidget(QLabel("(years back to show items)"))
        marketplace_settings_layout.addLayout(marketplace_years_layout)
        bulk_discount_layout = QHBoxLayout()
        bulk_discount_layout.addWidget(QLabel("Bulk Discount %:"))
        self.bulk_discount_spin = QDoubleSpinBox()
        self.bulk_discount_spin.setMinimum(0)
        self.bulk_discount_spin.setMaximum(100)
        self.bulk_discount_spin.setValue(MARKETPLACE_BULK_DISCOUNT_PERCENT * 100)
        self.bulk_discount_spin.setSuffix("%")
        bulk_discount_layout.addWidget(self.bulk_discount_spin)
        bulk_discount_layout.addWidget(QLabel("(discount when buying 10+ items)"))
        marketplace_settings_layout.addLayout(bulk_discount_layout)
        bulk_threshold_layout = QHBoxLayout()
        bulk_threshold_layout.addWidget(QLabel("Bulk Discount Threshold:"))
        self.bulk_threshold_spin = QSpinBox()
        self.bulk_threshold_spin.setMinimum(2)
        self.bulk_threshold_spin.setMaximum(50)
        self.bulk_threshold_spin.setValue(MARKETPLACE_BULK_DISCOUNT_THRESHOLD)
        bulk_threshold_layout.addWidget(self.bulk_threshold_spin)
        bulk_threshold_layout.addWidget(QLabel("(min items for bulk discount)"))
        marketplace_settings_layout.addLayout(bulk_threshold_layout)
        railcars_factor_layout = QHBoxLayout()
        railcars_factor_layout.addWidget(QLabel("Railcars per player factor:"))
        self.railcars_per_player_factor_spin = QDoubleSpinBox()
        self.railcars_per_player_factor_spin.setMinimum(0.5)
        self.railcars_per_player_factor_spin.setMaximum(10.0)
        self.railcars_per_player_factor_spin.setSingleStep(0.5)
        self.railcars_per_player_factor_spin.setValue(MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR)
        railcars_factor_layout.addWidget(self.railcars_per_player_factor_spin)
        railcars_factor_layout.addWidget(QLabel("(marketplace railcar count = players × factor)"))
        marketplace_settings_layout.addLayout(railcars_factor_layout)
        settings_tabs.addTab(_wrap_settings_tab_scroll(marketplace_settings_tab), "Marketplace")
        # --- Add settings tabs and action buttons ---
        self.settings_tabs = settings_tabs
        self.layout.addWidget(settings_tabs)
        self.layout.addWidget(QLabel(""))
        self.load_game_button = QPushButton("Load Game")
        self.load_game_button.clicked.connect(self.on_load_game_clicked)
        self.layout.addWidget(self.load_game_button)
        self.start_game_button = QPushButton("Start Game")
        self.start_game_button.clicked.connect(self.on_start_game_clicked)
        self.layout.addWidget(self.start_game_button)
    
    def rebuild_player_name_fields(self, count):
        from PyQt6.QtWidgets import QLineEdit, QHBoxLayout, QWidget
        if hasattr(self, 'human_count_spin') and self.human_count_spin.value() > count:
            self.human_count_spin.setValue(count)
        human_count = self.human_count_spin.value() if hasattr(self, 'human_count_spin') else count
        if hasattr(self, 'ai_watch_checkbox'):
            self.ai_watch_checkbox.setVisible(human_count < count)
        # Preserve existing player names and AI settings before rebuild (so adding a player doesn't reset them)
        saved_names = []
        saved_focus_by_ai_index = []  # focus currentText for each existing AI player (by AI index 0,1,...)
        saved_difficulty_by_ai_index = []
        old_count = len(self.name_edits)
        old_human_count = self.human_count_spin.value() if hasattr(self, 'human_count_spin') else 0
        for i in range(old_count):
            txt = self.name_edits[i].text().strip()
            saved_names.append(txt if txt else f"Player{i+1}")
            if i >= old_human_count and i < len(getattr(self, 'ai_focus_combos', [])) and self.ai_focus_combos[i]:
                saved_focus_by_ai_index.append(self.ai_focus_combos[i].currentText())
                saved_difficulty_by_ai_index.append(self.ai_difficulty_combos[i].currentText())
        while self.names_container.count():
            item = self.names_container.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self.name_edits.clear()
        self.ai_focus_combos.clear()
        if hasattr(self, 'ai_difficulty_combos'):
            self.ai_difficulty_combos.clear()
        human_count = self.human_count_spin.value() if hasattr(self, 'human_count_spin') else count
        focus_items = ["Random", "Lobbying", "Marketplace", "Line expansion", "Balanced"]
        difficulty_items = ["Easy", "Medium", "Hard"]
        for i in range(count):
            label = QLabel(f"Player {i+1} name:")
            edit = QLineEdit()
            name = saved_names[i] if i < len(saved_names) else f"Player{i+1}"
            edit.setText(name)
            edit.setPlaceholderText(f"Player{i+1}")
            self.names_container.addWidget(label)
            self.names_container.addWidget(edit)
            self.name_edits.append(edit)
            if i >= human_count:
                focus_row = QWidget()
                focus_layout = QHBoxLayout()
                focus_layout.setContentsMargins(0, 0, 0, 0)
                focus_row.setLayout(focus_layout)
                focus_layout.addWidget(QLabel(f"Player {i+1} (AI) focus:"))
                combo = QComboBox()
                combo.addItems(focus_items)
                ai_index = i - human_count
                focus_val = saved_focus_by_ai_index[ai_index] if ai_index < len(saved_focus_by_ai_index) else "Random"
                idx = focus_items.index(focus_val) if focus_val in focus_items else 0
                combo.setCurrentIndex(idx)
                focus_layout.addWidget(combo)
                focus_layout.addWidget(QLabel("Difficulty:"))
                diff_combo = QComboBox()
                diff_combo.addItems(difficulty_items)
                diff_val = saved_difficulty_by_ai_index[ai_index] if ai_index < len(saved_difficulty_by_ai_index) else (self.set_all_ai_difficulty_combo.currentText() if hasattr(self, 'set_all_ai_difficulty_combo') else "Hard")
                diff_idx = difficulty_items.index(diff_val) if diff_val in difficulty_items else 1
                diff_combo.setCurrentIndex(diff_idx)
                diff_combo.setToolTip("Hard = construction/marketplace discounts, lobby bonus. Easy = penalties.")
                focus_layout.addWidget(diff_combo)
                focus_layout.addStretch()
                self.names_container.addWidget(focus_row)
                self.ai_focus_combos.append(combo)
                self.ai_difficulty_combos.append(diff_combo)
            else:
                self.ai_focus_combos.append(None)
                self.ai_difficulty_combos.append(None)
    
    def _on_set_all_ai_difficulty_changed(self):
        """Apply selected difficulty to all AI combos instantly."""
        level = self.set_all_ai_difficulty_combo.currentText() if hasattr(self, 'set_all_ai_difficulty_combo') else "Hard"
        for combo in getattr(self, 'ai_difficulty_combos', []):
            if combo is not None:
                idx = combo.findText(level)
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)

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
        line_build_speed = self.line_build_speed_spin.value() if hasattr(self, 'line_build_speed_spin') else None
        construction_cost_multiplier = self.construction_cost_multiplier_spin.value() if hasattr(self, 'construction_cost_multiplier_spin') else None
        bulk_discount = (self.bulk_discount_spin.value() / 100.0) if hasattr(self, 'bulk_discount_spin') else None
        bulk_threshold = self.bulk_threshold_spin.value() if hasattr(self, 'bulk_threshold_spin') else None
        railcars_per_player_factor = self.railcars_per_player_factor_spin.value() if hasattr(self, 'railcars_per_player_factor_spin') else None
        total = len(names)
        human_count = min(
            self.human_count_spin.value() if hasattr(self, 'human_count_spin') else total,
            total
        )
        ai_count = max(0, total - human_count)
        ai_indices = list(range(human_count, total)) if ai_count > 0 else []
        ai_focus_map = {"Random": None, "Lobbying": "lobbying", "Marketplace": "marketplace", "Line expansion": "line_expansion", "Balanced": "balanced"}
        ai_difficulty_map = {"Easy": "easy", "Medium": "medium", "Hard": "hard"}
        ai_focus_per_player = {}
        ai_difficulty_per_player = {}
        for i in ai_indices:
            if i < len(getattr(self, 'ai_focus_combos', [])) and self.ai_focus_combos[i]:
                focus_val = ai_focus_map.get(self.ai_focus_combos[i].currentText(), None)
                if focus_val:
                    ai_focus_per_player[i] = focus_val
            if i < len(getattr(self, 'ai_difficulty_combos', [])) and self.ai_difficulty_combos[i]:
                diff_val = ai_difficulty_map.get(self.ai_difficulty_combos[i].currentText(), "hard")
                ai_difficulty_per_player[i] = diff_val
        ai_watch_enabled = ai_count > 0 and getattr(self, 'ai_watch_checkbox', None) and self.ai_watch_checkbox.isChecked()
        ai_debug_notifications_enabled = (
            getattr(self, 'ai_debug_notifications_checkbox', None) is None
            or self.ai_debug_notifications_checkbox.isChecked()
        )
        random_ai_spawn_enabled = getattr(self, 'random_ai_spawn_checkbox', None) and self.random_ai_spawn_checkbox.isChecked()
        random_ai_spawn_interval = self.random_ai_spawn_interval_spin.value() if hasattr(self, 'random_ai_spawn_interval_spin') else 30
        merge_enabled = getattr(self, 'merge_enabled_checkbox', None) and self.merge_enabled_checkbox.isChecked()
        merge_ai_ai_enabled = getattr(self, 'merge_ai_ai_checkbox', None) and self.merge_ai_ai_checkbox.isChecked()
        merge_ai_human_enabled = getattr(self, 'merge_ai_human_checkbox', None) and self.merge_ai_human_checkbox.isChecked()
        merge_human_human_enabled = getattr(self, 'merge_human_human_checkbox', None) and self.merge_human_human_checkbox.isChecked()
        inflation_rate = self.inflation_rate_spin.value() if hasattr(self, 'inflation_rate_spin') else 1.0
        profit_margin = self.profit_margin_spin.value() if hasattr(self, 'profit_margin_spin') else 1.0
        ai_min_population = self.ai_min_population_spin.value() if hasattr(self, 'ai_min_population_spin') else 100
        self.start_game_with_players(
            names, map_width, map_height, starting_money, worker_replenishment_rate,
            natural_disaster_frequency, marketplace_years_visible,
            line_build_speed_multiplier=line_build_speed,
            construction_cost_multiplier=construction_cost_multiplier,
            inflation_rate=inflation_rate,
            profit_margin=profit_margin,
            marketplace_bulk_discount_percent=bulk_discount,
            marketplace_bulk_discount_threshold=bulk_threshold,
            marketplace_railcars_per_player_factor=railcars_per_player_factor,
            ai_player_indices=ai_indices,
            ai_focus_per_player=ai_focus_per_player,
            ai_difficulty_per_player=ai_difficulty_per_player,
            ai_watch_enabled=ai_watch_enabled,
            ai_debug_notifications_enabled=ai_debug_notifications_enabled,
            random_ai_spawn_enabled=random_ai_spawn_enabled,
            random_ai_spawn_interval_years=random_ai_spawn_interval,
            merge_enabled=merge_enabled,
            merge_ai_ai_enabled=merge_ai_ai_enabled,
            merge_ai_human_enabled=merge_ai_human_enabled,
            merge_human_human_enabled=merge_human_human_enabled,
            ai_min_population=ai_min_population,
            merge_ai_healthy_profit_threshold_pct=self.merge_ai_healthy_threshold_spin.value() if hasattr(self, 'merge_ai_healthy_threshold_spin') else 25.0,
            merge_ai_reject_chance_pct=self.merge_ai_reject_chance_spin.value() if hasattr(self, 'merge_ai_reject_chance_spin') else 90.0,
            merge_ai_min_turns_to_live_for_accept=self.merge_ai_min_turns_to_live_spin.value() if hasattr(self, 'merge_ai_min_turns_to_live_spin') else 10,
            merge_ai_min_years_before_consider=self.merge_ai_min_years_before_consider_spin.value() if hasattr(self, 'merge_ai_min_years_before_consider_spin') else None,
            merge_ai_auto_fail_first_n_years=self.merge_ai_auto_fail_first_n_years_spin.value() if hasattr(self, 'merge_ai_auto_fail_first_n_years_spin') else None,
            war_start_chance_per_year=(self.war_start_chance_spin.value() / 100.0) if hasattr(self, 'war_start_chance_spin') else None,
            war_end_chance_per_year=(self.war_end_chance_spin.value() / 100.0) if hasattr(self, 'war_end_chance_spin') else None,
            war_worker_draft_chance_per_year=(self.war_draft_chance_spin.value() / 100.0) if hasattr(self, 'war_draft_chance_spin') else None,
            war_new_worker_cost_multiplier=self.war_worker_cost_multiplier_spin.value() if hasattr(self, 'war_worker_cost_multiplier_spin') else None,
        )

    def GetPlayerInfoButton(self):
        self.get_player_info_button = QPushButton("Get Player Info")
        self.get_player_info_button.clicked.connect(self.on_player_info_button_clicked)
        self.info_layout.addWidget(self.get_player_info_button)

    def start_game_with_players(self, player_names: list[str], map_width: int = 100, map_height: int = 100, starting_money: int = 50000, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, marketplace_years_visible: int = None, line_build_speed_multiplier: float = None, construction_cost_multiplier: float = None, marketplace_bulk_discount_percent: float = None, marketplace_bulk_discount_threshold: int = None, marketplace_railcars_per_player_factor: float = None, ai_player_indices: list[int] = None, ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, ai_watch_enabled: bool = False, ai_debug_notifications_enabled: bool = True, random_ai_spawn_enabled: bool = True, random_ai_spawn_interval_years: int = 30, merge_enabled: bool = True, merge_ai_ai_enabled: bool = True, merge_ai_human_enabled: bool = True, merge_human_human_enabled: bool = True, inflation_rate: float = 1.0, profit_margin: float = 1.0, default_ai_difficulty_for_spawns: str = None, ai_min_population: int = 100, merge_ai_healthy_profit_threshold_pct: float = 25.0, merge_ai_reject_chance_pct: float = 90.0, merge_ai_min_turns_to_live_for_accept: int = 10, merge_ai_min_years_before_consider: int = None, merge_ai_auto_fail_first_n_years: int = None, war_start_chance_per_year: float = None, war_end_chance_per_year: float = None, war_worker_draft_chance_per_year: float = None, war_new_worker_cost_multiplier: float = None):
        # Map selection from dynamic preset discovery (map type combo)
        combo_index = self.map_type_combo.currentIndex()
        map_preset_name = self._map_preset_names[combo_index] if combo_index < len(self._map_preset_names) else "standard"
        # Reload map preset from disk only when edit mode changed population files.
        if map_preset_name and getattr(self, '_map_preset_reload_needed', False):
            try:
                from map_preset_loader import reload_map_preset_from_disk
                reload_map_preset_from_disk(map_preset_name)
            except Exception:
                pass
            self._map_preset_reload_needed = False
        default_diff = default_ai_difficulty_for_spawns
        if default_diff is None and hasattr(self, 'set_all_ai_difficulty_combo'):
            t = self.set_all_ai_difficulty_combo.currentText()
            default_diff = {'Easy': 'easy', 'Medium': 'medium', 'Hard': 'hard'}.get(t, 'hard')
        from ruleset_loader import get_active_ruleset
        ruleset_name = (
            self.game_ruleset_combo.currentText()
            if hasattr(self, 'game_ruleset_combo') and self.game_ruleset_combo.currentText()
            else get_active_ruleset()
        )
        self.operations = Operations(
            player_names, map_width, map_height, starting_money,
            worker_replenishment_rate, natural_disaster_frequency,
            ruleset_name=ruleset_name,
            map_preset_name=map_preset_name,
            marketplace_years_visible=marketplace_years_visible,
            line_build_speed_multiplier=line_build_speed_multiplier,
            construction_cost_multiplier=construction_cost_multiplier,
            marketplace_bulk_discount_percent=marketplace_bulk_discount_percent,
            marketplace_bulk_discount_threshold=marketplace_bulk_discount_threshold,
            marketplace_railcars_per_player_factor=marketplace_railcars_per_player_factor,
            ai_player_indices=ai_player_indices,
            ai_focus_per_player=ai_focus_per_player,
            ai_difficulty_per_player=ai_difficulty_per_player or {},
            random_ai_spawn_enabled=random_ai_spawn_enabled,
            random_ai_spawn_interval_years=random_ai_spawn_interval_years,
            merge_enabled=merge_enabled,
            merge_ai_ai_enabled=merge_ai_ai_enabled,
            merge_ai_human_enabled=merge_ai_human_enabled,
            merge_human_human_enabled=merge_human_human_enabled,
            inflation_rate=inflation_rate,
            profit_margin=profit_margin,
            default_ai_difficulty_for_spawns=default_diff or 'hard',
            ai_min_population=ai_min_population,
            merge_ai_healthy_profit_threshold_pct=merge_ai_healthy_profit_threshold_pct,
            merge_ai_reject_chance_pct=merge_ai_reject_chance_pct,
            merge_ai_min_turns_to_live_for_accept=merge_ai_min_turns_to_live_for_accept,
            merge_ai_min_years_before_consider=merge_ai_min_years_before_consider,
            merge_ai_auto_fail_first_n_years=merge_ai_auto_fail_first_n_years,
            war_start_chance_per_year=war_start_chance_per_year,
            war_end_chance_per_year=war_end_chance_per_year,
            war_worker_draft_chance_per_year=war_worker_draft_chance_per_year,
            war_new_worker_cost_multiplier=war_new_worker_cost_multiplier,
            ai_debug_notifications_enabled=ai_debug_notifications_enabled,
        )
        from inflation_utils import set_inflation_rate_multiplier
        set_inflation_rate_multiplier(inflation_rate)
        self.ai_watch_enabled = ai_watch_enabled
        
        if self.status_bar is None:
            self._attach_game_status_bar(self.centralWidget().layout())
        self.save_game_action.setEnabled(True)
        self.advance_year_action.setEnabled(True)
        self.refresh_displays_action.setEnabled(True)
        if hasattr(self, 'game_settings_action'):
            self.game_settings_action.setEnabled(True)
        if hasattr(self, 'propose_merge_action'):
            self._update_propose_merge_action()
        
        title = "Start Game"
        players_str = ", ".join([self.operations.get_player_name(player) for player in self.operations.get_players()])
        text = f"The game has started. The players are:\n{players_str}"
        QMessageBox.information(self, title, text)
        try:
            self.tabs.removeTab(0)
        except Exception:
            pass
        self._setup_game_tabs(initial_player_index=0)
        self.add_message(f"Game started. Players: {players_str}.")
        QTimer.singleShot(100, self._start_background_ai_in_year_turns)
        QTimer.singleShot(150, self._maybe_auto_advance_year_if_ai)

    def _is_current_player_ai(self) -> bool:
        """True if current player is AI and not eliminated."""
        if not self.operations:
            return False
        cp = self.operations.get_current_player(self.current_player_index)
        return cp is not None and getattr(cp, 'is_ai', False) and not self.operations.is_player_eliminated(cp)

    def _maybe_auto_advance_year_if_ai(self):
        """All-AI games: run in-year turns in background, then end the year when every AI is done."""
        if not self.operations or self.is_network_mode or getattr(self, '_advance_year_in_progress', False):
            return
        active = self.operations.get_active_players()
        if not active:
            return
        if not self._is_current_player_ai():
            return
        self._year_end_pending = True
        if self.operations.get_ai_players_needing_in_year_turn():
            self._start_background_ai_in_year_turns()
            self._update_year_end_waiting_ui()
            return
        QTimer.singleShot(50, self._try_complete_year_end)

    def _start_background_ai_in_year_turns(self):
        """Run pending AI in-year turns in a background thread (parallel with human play)."""
        if not self.operations or self.is_network_mode:
            return
        if not self.operations.get_ai_players_needing_in_year_turn():
            return
        thread = getattr(self, '_ai_in_year_thread', None)
        if thread is not None and thread.isRunning():
            return
        self._ai_in_year_thread = AIInYearTurnsThread(self.operations)
        self._ai_in_year_thread.ai_progress.connect(self._on_ai_progress)
        self._ai_in_year_thread.finished_turns.connect(self._on_background_ai_in_year_turns_finished)
        self._ai_in_year_thread.start()

    def _on_background_ai_in_year_turns_finished(self, ai_results_list, ai_build_failures):
        if ai_build_failures:
            self._show_ai_build_failure_popup(ai_build_failures)
        if ai_results_list and hasattr(self, 'ai_watch_text') and self.ai_watch_text:
            for player, acts in ai_results_list:
                if acts:
                    self.ai_watch_text.append(f"{player.get_name()}: {len(acts)} action(s) (in-year)")
                else:
                    self.ai_watch_text.append(f"{player.get_name()}: No actions")
        self.refresh_all_displays()
        self._update_year_end_waiting_ui()
        QTimer.singleShot(50, self._try_complete_year_end)
        if self.operations.get_ai_players_needing_in_year_turn():
            QTimer.singleShot(50, self._start_background_ai_in_year_turns)
        else:
            QTimer.singleShot(100, self._maybe_auto_advance_year_if_ai)

    def _humans_to_mark_ready_on_end_year(self) -> list:
        """Human player(s) signaled ready by the current End Year click."""
        if not self.operations:
            return []
        if getattr(self, 'is_network_mode', False) and hasattr(self, 'assigned_player_id'):
            players = self.operations.get_players()
            if 0 <= self.assigned_player_id < len(players):
                p = players[self.assigned_player_id]
                if not getattr(p, 'is_ai', False) and not self.operations.is_player_eliminated(p):
                    return [p]
            return []
        cp = self.operations.get_current_player(self.current_player_index)
        if cp and not getattr(cp, 'is_ai', False) and not self.operations.is_player_eliminated(cp):
            return [cp]
        return []

    def _update_year_end_waiting_ui(self):
        """Status while End Year is requested but not all players have finished."""
        if not self.operations:
            return
        active = self.operations.get_active_players()
        done = sum(1 for p in active if self.operations.player_year_actions_completed(p))
        total = len(active)
        pending_ai = len(self.operations.get_ai_players_needing_in_year_turn())
        waiting_humans = [
            p.get_name() for p in active
            if not getattr(p, 'is_ai', False) and not self.operations.human_ready_for_year_end(p)
        ]
        if getattr(self, '_year_end_pending', False) and done < total:
            parts = [f"Ending year ({done}/{total} ready)"]
            if pending_ai:
                parts.append(f"{pending_ai} AI(s) still playing")
            if waiting_humans:
                parts.append(f"waiting: {', '.join(waiting_humans)}")
            text = " — ".join(parts)
            if self.status_end_year_timer_label:
                self.status_end_year_timer_label.setVisible(True)
                self.status_end_year_timer_label.setText(text)
        elif self.status_end_year_timer_label and not getattr(self, '_advance_year_in_progress', False):
            self.status_end_year_timer_label.setVisible(False)
            self.status_end_year_timer_label.setText("")
        self._update_end_year_button_state()

    def _update_end_year_button_state(self):
        if not self.status_end_year_btn:
            return
        if getattr(self, '_advance_year_in_progress', False):
            self.status_end_year_btn.setEnabled(False)
            return
        for p in self._humans_to_mark_ready_on_end_year():
            if self.operations and self.operations.human_ready_for_year_end(p):
                self.status_end_year_btn.setEnabled(False)
                return
        self.status_end_year_btn.setEnabled(True)

    def _request_year_end(self):
        """Register End Year for the active human and wait until every player has finished."""
        for player in self._humans_to_mark_ready_on_end_year():
            self.operations.mark_human_ready_for_year_end(player)
        self._year_end_pending = True
        self._update_year_end_waiting_ui()
        self._start_background_ai_in_year_turns()
        self._try_complete_year_end()

    def _try_complete_year_end(self):
        """Run advance_year_complete once every active player has finished the year."""
        if not getattr(self, '_year_end_pending', False):
            return
        if not self.operations or getattr(self, '_advance_year_in_progress', False):
            return
        if not self.operations.all_players_completed_year_actions():
            return
        ai_thread = getattr(self, '_ai_in_year_thread', None)
        if ai_thread is not None and ai_thread.isRunning():
            return
        self._launch_advance_year_thread()

    def start_networked_game(self, game, assigned_player_id: int, network_client, network_thread, network_session_id: str, all_players_info: list):
        """Start game UI with server-provided game state. Used when joining/creating a networked game."""
        while self.tabs.count() > 0:
            self.tabs.removeTab(0)
        self.is_network_mode = True
        self.network_client = network_client
        self.network_thread = network_thread
        self.network_session_id = network_session_id
        self.assigned_player_id = assigned_player_id
        self.current_player_index = assigned_player_id
        player_names = [p.get_name() for p in game.get_players()]
        game_map = game.get_map()
        map_width = game_map.width if hasattr(game_map, 'width') else 100
        map_height = game_map.height if hasattr(game_map, 'height') else 75
        map_preset_name = getattr(game_map, 'map_preset_name', None)
        use_osm = getattr(game_map, 'use_osm', False)
        map_center_lat = getattr(game_map, 'map_center_lat', None)
        map_center_lon = getattr(game_map, 'map_center_lon', None)
        self.operations = Operations(player_names, map_width, map_height, 20000, 1.0,
                                     use_osm=use_osm, map_center_lat=map_center_lat,
                                     map_center_lon=map_center_lon, map_preset_name=map_preset_name)
        self.operations.game = game
        if network_client:
            network_client.game_state_received.connect(self._on_network_game_state_received)
            network_client.error_received.connect(self._on_network_error)
            network_client.player_joined.connect(self._on_network_player_joined)
            if hasattr(network_client, 'player_left'):
                network_client.player_left.connect(self._on_network_player_left)
            if hasattr(network_client, 'year_advanced'):
                network_client.year_advanced.connect(self._on_network_year_advanced)
            if hasattr(network_client, 'end_year_status'):
                network_client.end_year_status.connect(self._on_network_end_year_status)
            if hasattr(network_client, 'chat_received'):
                network_client.chat_received.connect(lambda d: self.add_message(f"{d.get('sender', '?')}: {d.get('message', '')}"))
        self._setup_game_tabs(initial_player_index=assigned_player_id)
        if all_players_info:
            players_list = "\n".join([f"  - {p['player_name']} (Player {p['player_id'] + 1})" for p in all_players_info])
            player_name = game.get_players()[assigned_player_id].get_name()
            QMessageBox.information(self, "Network Game Started",
                f"Welcome {player_name}!\n\n"
                f"Session ID: {network_session_id}\n\n"
                f"Connected Players:\n{players_list}\n\n"
                f"You are controlling: {player_name}")

    def _on_network_game_state_received(self, data):
        """Handle game state update from server."""
        game_state = data.get('game_state', {})
        action_result = data.get('action_result', {})
        game = self.network_client.deserialize_game_state(game_state)
        if game and self.operations:
            self.operations.game = game
            # Sync session-deleted points to new map so removed points stay removed
            game_map = game.get_map()
            if game_map and hasattr(self.operations, '_session_deleted_population_points') and self.operations._session_deleted_population_points:
                if not hasattr(game_map, '_deleted_population_points'):
                    game_map._deleted_population_points = set()
                game_map._deleted_population_points |= self.operations._session_deleted_population_points
                # Also remove from population dict so they never reappear (belt-and-suspenders)
                if hasattr(game_map, 'population') and game_map.population is not None:
                    for pt in self.operations._session_deleted_population_points:
                        game_map.population.pop(pt, None)
            if self.status_year_label:
                try:
                    self.status_year_label.setText(f"Year: {game.get_year()}")
                except Exception:
                    pass
            if action_result.get('success') and action_result.get('message'):
                self.add_message(action_result['message'])
            self.refresh_all_displays()

    def _on_network_error(self, message):
        """Handle network error."""
        QMessageBox.warning(self, "Network Error", message)

    def _on_network_player_joined(self, data):
        """Handle player joining."""
        player_name = data.get('player_name')
        QMessageBox.information(self, "Player Joined", f"{player_name} joined the game.")

    def _on_network_player_left(self, data):
        """Handle player leaving."""
        player_name = data.get('player_name')
        QMessageBox.information(self, "Player Left", f"{player_name} left the game.")

    def _on_network_year_advanced(self, data):
        """Handle year advanced notification."""
        new_year = data.get('new_year', 0)
        message = data.get('message', f'Year advanced to {new_year}')
        game_state_data = data.get('game_state')
        if game_state_data and self.network_client:
            game = self.network_client.deserialize_game_state(game_state_data)
            if game and self.operations:
                self.operations.game = game
                game_map = game.get_map()
                if game_map and hasattr(self.operations, '_session_deleted_population_points') and self.operations._session_deleted_population_points:
                    if not hasattr(game_map, '_deleted_population_points'):
                        game_map._deleted_population_points = set()
                    game_map._deleted_population_points |= self.operations._session_deleted_population_points
                    if hasattr(game_map, 'population') and game_map.population is not None:
                        for pt in self.operations._session_deleted_population_points:
                            game_map.population.pop(pt, None)
        if self.operations:
            current_year = self.operations.get_year()
            if self.status_year_label:
                self.status_year_label.setText(f"Year: {current_year}")
        self._sync_missing_competitor_tabs_from_operations()
        self.refresh_all_displays()
        QMessageBox.information(self, "Year Advanced", message)

    def _on_network_end_year_status(self, data):
        """Handle end year status update."""
        ready_count = data.get('ready_count', 0)
        total_count = data.get('total_count', 1)
        ready_players = data.get('ready_players', [])
        if self.status_year_label and self.operations:
            current_year = self.operations.get_year()
            if ready_count < total_count:
                status_text = f"Year: {current_year} ({ready_count}/{total_count} ready to end year)"
            else:
                status_text = f"Year: {current_year}"
            self.status_year_label.setText(status_text)
        if ready_count < total_count:
            status_msg = f"Waiting for other players to end year ({ready_count}/{total_count} ready: {', '.join(ready_players)})"
            if self.statusBar():
                self.statusBar().showMessage(status_msg, 3000)

    def _setup_game_tabs(self, initial_player_index: int = 0):
        """Shared setup for game tabs - used by both local and networked play."""
        players = self.operations.get_players()
        self.current_player_index = initial_player_index
        self._game_messages = []
        self._message_widgets = []
        # Always ensure status_messages_list is in _message_widgets when it exists
        if hasattr(self, 'status_messages_list') and self.status_messages_list is not None:
            self._message_widgets.append(self.status_messages_list)
        if self.status_bar is None:
            self._attach_game_status_bar(self.centralWidget().layout())
            self.save_game_action.setEnabled(True)
            self.advance_year_action.setEnabled(True)
            self.refresh_displays_action.setEnabled(True)
            if hasattr(self, 'game_settings_action'):
                self.game_settings_action.setEnabled(True)
        # Clear stale refs before creating new tabs to avoid deleted layout errors
        self.player_tabs_dict = {}
        self._player_tabs_keepalive = []
        # Our player: assigned in network mode, first in local mode
        our_player_id = initial_player_index if initial_player_index < len(players) else self.operations.get_player_id(players[0])
        other_player_ids = [self.operations.get_player_id(p) for p in players if self.operations.get_player_id(p) != our_player_id]
        self._our_player_id = our_player_id
        self._other_player_ids = other_player_ids
        self._all_player_indices = [our_player_id] + other_player_ids  # our first, then others
        # Tab 0: Our Player (full tabs)
        our_player = next((p for p in players if self.operations.get_player_id(p) == our_player_id), players[0])
        our_player_tabs = self._create_player_tabs(our_player, for_other_player=False)
        our_tab_idx = self.tabs.addTab(our_player_tabs, our_player.get_name())
        self.tabs.setTabIcon(our_tab_idx, player_color_icon(self.operations.get_player_color_index(our_player)))
        # Tab 1: Other Players (parent with read-only subtabs)
        if other_player_ids:
            other_container = QTabWidget()
            for pid in other_player_ids:
                other_player = next((p for p in players if self.operations.get_player_id(p) == pid), None)
                if other_player:
                    other_tabs = self._create_player_tabs(other_player, for_other_player=True)
                    sub_idx = other_container.addTab(other_tabs, other_player.get_name())
                    other_container.setTabIcon(sub_idx, player_color_icon(self.operations.get_player_color_index(other_player)))
            self._other_players_container = other_container
            self.tabs.addTab(other_container, "Other Players")
        else:
            self._other_players_container = None
        if getattr(self, 'ai_watch_enabled', False) and any(getattr(p, 'is_ai', False) for p in players):
            self._create_ai_watch_tab()
        self._create_news_log_tab()
        self._create_rules_tab()
        self._create_vehicles_tab()
        if players:
            ref_player = our_player
            ref_id = our_player_id
            self.assets_tab = self.player_tabs_dict[ref_id]['assets_tab']
            self.assets_subtabs = self.player_tabs_dict[ref_id]['assets_subtabs']
            self.lines_tab = self.player_tabs_dict[ref_id]['lines_tab']
            self.lines_layout = self.player_tabs_dict[ref_id]['lines_layout']
            self.rolling_stock_tab = self.player_tabs_dict[ref_id]['rolling_stock_tab']
            self.rolling_stock_tabs = self.player_tabs_dict[ref_id]['rolling_stock_tabs']
            self.rolling_stock_trainsets_tab = self.player_tabs_dict[ref_id]['rolling_stock_trainsets_tab']
            self.rolling_stock_trainsets_layout = self.player_tabs_dict[ref_id]['rolling_stock_trainsets_layout']
            self.rolling_stock_locomotives_tab = self.player_tabs_dict[ref_id]['rolling_stock_locomotives_tab']
            self.rolling_stock_locomotives_layout = self.player_tabs_dict[ref_id]['rolling_stock_locomotives_layout']
            self.rolling_stock_railcars_tab = self.player_tabs_dict[ref_id]['rolling_stock_railcars_tab']
            self.rolling_stock_railcars_layout = self.player_tabs_dict[ref_id]['rolling_stock_railcars_layout']
            self.workers_tab = self.player_tabs_dict[ref_id]['workers_tab']
            self.workers_layout = self.player_tabs_dict[ref_id]['workers_layout']
            self.law_tab = self.player_tabs_dict[ref_id]['law_tab']
            self.law_layout = self.player_tabs_dict[ref_id]['law_layout']
            self.government_tab = self.player_tabs_dict[ref_id]['government_tab']
            self.government_layout = self.player_tabs_dict[ref_id]['government_layout']
        for player in players:
            self.DisplayRollingStockInfo(player)
        self.DisplayMarketplaceInfo()
        self.DisplayMapInfo()
        for player in players:
            self.DisplayLinesControls(player)
            self.DisplayWorkersControls(player)
            self.DisplayGovernmentInfo(player)
            try:
                self.DisplayLawInfo(player)
            except Exception as e:
                player_id = self.operations.get_player_id(player)
                if player_id in self.player_tabs_dict:
                    law_layout = self.player_tabs_dict[player_id].get('law_layout')
                    if law_layout:
                        law_layout.addWidget(QLabel(f"Error loading lawsuit info: {str(e)}"))
        self.refresh_construction_tab()
        self._apply_view_tab_visibility()
        self.update_status_bar()
        # Set initial tab: our player is always tab 0
        self.tabs.setCurrentIndex(0)

        try:
            self.tabs.currentChanged.disconnect()
        except TypeError:
            pass
        self.tabs.currentChanged.connect(self._on_game_session_main_tab_changed)
        if self._other_players_container is not None:
            try:
                self._other_players_container.currentChanged.disconnect()
            except TypeError:
                pass
            self._other_players_container.currentChanged.connect(self._on_game_session_other_subtab_changed)
        self._apply_view_only_state()
        self._update_game_session_tab_menu_actions()
        self._remove_assigned_player_tab_close_button()
        QTimer.singleShot(100, self._start_background_ai_in_year_turns)

    def _game_session_update_refs_for_player(self, ref_id):
        if ref_id in self.player_tabs_dict:
            d = self.player_tabs_dict[ref_id]
            self.assets_tab = d.get('assets_tab')
            self.assets_subtabs = d.get('assets_subtabs')
            self.lines_tab = d.get('lines_tab')
            self.lines_layout = d.get('lines_layout')
            self.rolling_stock_tab = d.get('rolling_stock_tab')
            self.rolling_stock_tabs = d.get('rolling_stock_tabs')
            self.rolling_stock_trainsets_tab = d.get('rolling_stock_trainsets_tab')
            self.rolling_stock_trainsets_layout = d.get('rolling_stock_trainsets_layout')
            self.rolling_stock_locomotives_tab = d.get('rolling_stock_locomotives_tab')
            self.rolling_stock_locomotives_layout = d.get('rolling_stock_locomotives_layout')
            self.rolling_stock_railcars_tab = d.get('rolling_stock_railcars_tab')
            self.rolling_stock_railcars_layout = d.get('rolling_stock_railcars_layout')
            self.workers_tab = d.get('workers_tab')
            self.workers_layout = d.get('workers_layout')
            self.law_tab = d.get('law_tab')
            self.law_layout = d.get('law_layout')
            self.government_tab = d.get('government_tab')
            self.government_layout = d.get('government_layout')
        self._apply_view_only_state()

    def _on_game_session_main_tab_changed(self, idx):
        our_player_id = getattr(self, '_our_player_id', None)
        if our_player_id is None:
            return
        if idx == 0:
            self.current_player_index = our_player_id
            self._game_session_update_refs_for_player(our_player_id)
        elif idx == 1 and getattr(self, '_other_players_container', None) is not None:
            inner_idx = self._other_players_container.currentIndex()
            opids = getattr(self, '_other_player_ids', None) or []
            if inner_idx >= 0 and inner_idx < len(opids):
                self.current_player_index = opids[inner_idx]
                self._game_session_update_refs_for_player(opids[inner_idx])
        else:
            self._game_session_update_refs_for_player(our_player_id)

    def _on_game_session_other_subtab_changed(self, inner_idx):
        if self.tabs.currentIndex() != 1 or getattr(self, '_other_players_container', None) is None:
            return
        opids = getattr(self, '_other_player_ids', None) or []
        if inner_idx < 0 or inner_idx >= len(opids):
            return
        self.current_player_index = opids[inner_idx]
        self._game_session_update_refs_for_player(opids[inner_idx])
        players_live = self.operations.get_players() if self.operations else []
        other_player = next((p for p in players_live if self.operations.get_player_id(p) == opids[inner_idx]), None)
        if other_player and hasattr(self, 'DisplayLinesControls'):
            self.DisplayLinesControls(other_player)

    def _register_midgame_other_player_ui(self, new_player):
        """Add Other Players sub-tabs for a competitor created after session start (e.g. random AI spawn)."""
        if not getattr(self, 'operations', None) or not getattr(self, 'tabs', None):
            return
        our_id = getattr(self, '_our_player_id', None)
        if our_id is None or new_player is None:
            return
        new_id = self.operations.get_player_id(new_player)
        if new_id in self.player_tabs_dict or new_id == our_id:
            return
        op = list(getattr(self, '_other_player_ids', None) or [])
        if new_id not in op:
            op.append(new_id)
        self._other_player_ids = op
        api = list(getattr(self, '_all_player_indices', None) or [])
        if new_id not in api:
            api.append(new_id)
        self._all_player_indices = api
        other_tabs = self._create_player_tabs(new_player, for_other_player=True)
        container = getattr(self, '_other_players_container', None)
        if container is not None:
            sub_idx = container.addTab(other_tabs, new_player.get_name())
            container.setTabIcon(sub_idx, player_color_icon(self.operations.get_player_color_index(new_player)))
        else:
            self._other_players_container = QTabWidget()
            sub_idx = self._other_players_container.addTab(other_tabs, new_player.get_name())
            self._other_players_container.setTabIcon(sub_idx, player_color_icon(self.operations.get_player_color_index(new_player)))
            self._other_players_container.currentChanged.connect(self._on_game_session_other_subtab_changed)
            self.tabs.insertTab(1, self._other_players_container, "Other Players")
        self.DisplayRollingStockInfo(new_player)
        self.DisplayLinesControls(new_player)
        self.DisplayWorkersControls(new_player)
        self.DisplayGovernmentInfo(new_player)
        try:
            self.DisplayLawInfo(new_player)
        except Exception:
            pass
        self._apply_view_tab_visibility()
        self._apply_view_only_state()

    def _sync_missing_competitor_tabs_from_operations(self):
        """Ensure every non-eliminated competitor has a player_tabs_dict entry (spawn / network resync)."""
        if not getattr(self, 'operations', None) or not getattr(self, 'tabs', None):
            return
        our_id = getattr(self, '_our_player_id', None)
        if our_id is None:
            return
        for p in self.operations.get_players():
            if self.operations.is_player_eliminated(p):
                continue
            pid = self.operations.get_player_id(p)
            if pid == our_id or pid in self.player_tabs_dict:
                continue
            self._register_midgame_other_player_ui(p)

    def _update_game_session_tab_menu_actions(self):
        """Enable Game → Open News Log / Open AI Watch when those tabs exist."""
        if not hasattr(self, 'game_open_news_log_action'):
            return
        has_news = False
        has_ai_watch = False
        if getattr(self, 'tabs', None) is not None:
            for i in range(self.tabs.count()):
                try:
                    t = self.tabs.tabText(i)
                except Exception:
                    continue
                if t == "News Log":
                    has_news = True
                elif t == "AI Watch":
                    has_ai_watch = True
        self.game_open_news_log_action.setEnabled(bool(has_news))
        self.game_open_ai_watch_action.setEnabled(bool(has_ai_watch))

    def _focus_main_tab_by_title(self, title: str) -> bool:
        """Show and focus a top-level tab by title."""
        if not getattr(self, 'tabs', None):
            return False
        for i in range(self.tabs.count()):
            try:
                if self.tabs.tabText(i) != title:
                    continue
            except Exception:
                continue
            try:
                self.tabs.setTabVisible(i, True)
            except Exception:
                pass
            self.tabs.setCurrentIndex(i)
            return True
        return False

    def _open_main_tab_by_title(self, title: str):
        self._focus_main_tab_by_title(title)

    def _open_current_player_tab(self):
        """Open/focus the currently active player's top-level tab."""
        if not getattr(self, 'tabs', None):
            return
        if not getattr(self, 'operations', None):
            return
        cp = self.operations.get_current_player(self.current_player_index)
        if cp is None:
            return
        self._focus_main_tab_by_title(self.operations.get_player_name(cp))

    def _open_active_player_subtab(self, subtab_name: str):
        """Open/focus a subtab in the active player's tab container."""
        if not getattr(self, 'operations', None):
            return
        player_id = self.current_player_index
        d = self.player_tabs_dict.get(player_id, {})
        tabs = d.get('player_tabs')
        if tabs is None:
            return

        def _norm_tab_title(t: str) -> str:
            return (t or '').replace('&', '').strip()

        want = _norm_tab_title(subtab_name)
        matched = False
        for j in range(tabs.count()):
            try:
                if _norm_tab_title(tabs.tabText(j)) != want:
                    continue
                tabs.setTabVisible(j, True)
                tabs.setCurrentIndex(j)
                matched = True
                break
            except Exception:
                continue
        # Map: fallback to index captured at creation (avoids title mismatch / visibility edge cases).
        if not matched and want == 'Map':
            mi = d.get('map_tab_index')
            if isinstance(mi, int) and 0 <= mi < tabs.count():
                try:
                    tabs.setTabVisible(mi, True)
                    tabs.setCurrentIndex(mi)
                    matched = True
                except Exception:
                    pass
        if matched and want == 'Map':
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda pid=player_id: self._ensure_map_web_view_ready(pid))
        # Ensure we're actually viewing the selected player's top-level tab.
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w is tabs:
                self.tabs.setCurrentIndex(i)
                return
            if w is getattr(self, '_other_players_container', None):
                for inner_idx, p in enumerate(getattr(self, '_other_player_ids', [])):
                    if p == player_id:
                        self.tabs.setCurrentIndex(i)
                        try:
                            self._other_players_container.setCurrentIndex(inner_idx)
                        except Exception:
                            pass
                        return

    def _on_game_open_news_log(self):
        if self._focus_main_tab_by_title("News Log"):
            self._refresh_news_log_tab()

    def _on_game_open_ai_watch(self):
        self._focus_main_tab_by_title("AI Watch")

    def _show_in_game_settings_dialog(self):
        """Open a dialog to change runtime game settings (AI, economy, mergers, marketplace)."""
        if not self.operations:
            return
        from PyQt6.QtWidgets import QDialogButtonBox

        current = self.operations.get_runtime_game_settings()
        has_ai = any(getattr(p, 'is_ai', False) for p in self.operations.get_players())

        dlg = QDialog(self)
        dlg.setWindowTitle("Game Settings")
        dlg.setMinimumWidth(520)
        outer = QVBoxLayout(dlg)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)

        ai_group = QGroupBox("AI")
        ai_layout = QVBoxLayout(ai_group)
        ai_watch_cb = QCheckBox("Enable AI Watch tab")
        ai_watch_cb.setChecked(bool(getattr(self, 'ai_watch_enabled', False)))
        ai_watch_cb.setEnabled(has_ai)
        ai_watch_cb.setToolTip("Show the AI Watch tab to monitor AI actions during End Year.")
        ai_layout.addWidget(ai_watch_cb)
        ai_debug_cb = QCheckBox("AI debug notifications")
        ai_debug_cb.setChecked(bool(current.get('ai_debug_notifications_enabled', True)))
        ai_layout.addWidget(ai_debug_cb)
        random_spawn_cb = QCheckBox("Random AI spawn (new AI joins periodically)")
        random_spawn_cb.setChecked(bool(current.get('random_ai_spawn_enabled', False)))
        ai_layout.addWidget(random_spawn_cb)
        spawn_row = QHBoxLayout()
        spawn_row.addWidget(QLabel("Spawn interval (years):"))
        spawn_interval_spin = QSpinBox()
        spawn_interval_spin.setRange(5, 200)
        spawn_interval_spin.setValue(int(current.get('random_ai_spawn_interval_years', 30)))
        spawn_row.addWidget(spawn_interval_spin)
        spawn_row.addStretch()
        ai_layout.addLayout(spawn_row)
        min_pop_row = QHBoxLayout()
        min_pop_row.addWidget(QLabel("AI minimum endpoint population:"))
        ai_min_pop_spin = QSpinBox()
        ai_min_pop_spin.setRange(0, 10000)
        ai_min_pop_spin.setValue(int(current.get('ai_min_population', 100)))
        min_pop_row.addWidget(ai_min_pop_spin)
        min_pop_row.addStretch()
        ai_layout.addLayout(min_pop_row)
        layout.addWidget(ai_group)

        economy_group = QGroupBox("Economy & events")
        economy_layout = QVBoxLayout(economy_group)

        def _add_double_row(parent_layout, label, value, minimum, maximum, step=0.1, decimals=2, suffix=""):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setValue(float(value))
            if suffix:
                spin.setSuffix(suffix)
            row.addWidget(spin)
            row.addStretch()
            parent_layout.addLayout(row)
            return spin

        inflation_spin = _add_double_row(economy_layout, "Inflation rate:", current.get('inflation_rate', 1.0), 0.25, 3.0, 0.25)
        profit_spin = _add_double_row(economy_layout, "Profit margin:", current.get('profit_margin', 1.0), 0.25, 5.0, 0.25)
        replen_spin = _add_double_row(economy_layout, "Worker replenishment:", current.get('worker_replenishment_rate', 1.0), 0.1, 5.0, 0.1, 1)
        disaster_spin = _add_double_row(economy_layout, "Disaster frequency:", current.get('natural_disaster_frequency', 1.0), 0.1, 5.0, 0.1, 1)
        build_speed_spin = _add_double_row(economy_layout, "Line build speed:", current.get('line_build_speed_multiplier', 1.0), 0.5, 5.0, 0.1, 1)
        construction_cost_spin = _add_double_row(
            economy_layout,
            "Construction cost multiplier:",
            current.get('construction_cost_multiplier', 2.0),
            0.5, 10.0, 0.5, 1,
        )
        construction_cost_spin.setToolTip(
            "Multiplier for all construction (lines, extensions, stations, upgrades, ferries)."
        )
        layout.addWidget(economy_group)

        merge_group = QGroupBox("Mergers")
        merge_layout = QVBoxLayout(merge_group)
        merge_enabled_cb = QCheckBox("Allow mergers (master switch)")
        merge_enabled_cb.setChecked(bool(current.get('merge_enabled', False)))
        merge_layout.addWidget(merge_enabled_cb)
        merge_hh_cb = QCheckBox("Allow mergers between humans")
        merge_hh_cb.setChecked(bool(current.get('merge_human_human_enabled', True)))
        merge_layout.addWidget(merge_hh_cb)
        merge_aa_cb = QCheckBox("Allow mergers between AIs")
        merge_aa_cb.setChecked(bool(current.get('merge_ai_ai_enabled', True)))
        merge_layout.addWidget(merge_aa_cb)
        merge_ah_cb = QCheckBox("Allow mergers between humans and AIs")
        merge_ah_cb.setChecked(bool(current.get('merge_ai_human_enabled', True)))
        merge_layout.addWidget(merge_ah_cb)
        merge_healthy_spin = _add_double_row(
            merge_layout, "AI healthy profit threshold:", current.get('merge_ai_healthy_profit_threshold_pct', 25.0), 0, 100, 1, 0, "%",
        )
        merge_reject_spin = _add_double_row(
            merge_layout, "AI reject merger chance:", current.get('merge_ai_reject_chance_pct', 90.0), 0, 100, 1, 0, "%",
        )
        merge_ttl_row = QHBoxLayout()
        merge_ttl_row.addWidget(QLabel("AI auto-reject if turns to live >"))
        merge_ttl_spin = QSpinBox()
        merge_ttl_spin.setRange(0, 200)
        merge_ttl_spin.setValue(int(current.get('merge_ai_min_turns_to_live_for_accept', 10)))
        merge_ttl_row.addWidget(merge_ttl_spin)
        merge_ttl_row.addStretch()
        merge_layout.addLayout(merge_ttl_row)
        merge_years_row = QHBoxLayout()
        merge_years_row.addWidget(QLabel("AI years before considering merge:"))
        merge_years_spin = QSpinBox()
        merge_years_spin.setRange(0, 50)
        merge_years_spin.setValue(int(current.get('merge_ai_min_years_before_consider', 0)))
        merge_years_row.addWidget(merge_years_spin)
        merge_years_row.addWidget(QLabel("Auto-fail AI merges first N years:"))
        merge_fail_spin = QSpinBox()
        merge_fail_spin.setRange(0, 50)
        merge_fail_spin.setValue(int(current.get('merge_ai_auto_fail_first_n_years', 0)))
        merge_years_row.addWidget(merge_fail_spin)
        merge_years_row.addStretch()
        merge_layout.addLayout(merge_years_row)
        layout.addWidget(merge_group)

        market_group = QGroupBox("Marketplace")
        market_layout = QVBoxLayout(market_group)
        mp_years_row = QHBoxLayout()
        mp_years_row.addWidget(QLabel("Years visible:"))
        mp_years_spin = QSpinBox()
        mp_years_spin.setRange(1, 200)
        mp_years_spin.setValue(int(current.get('marketplace_years_visible', 40)))
        mp_years_row.addWidget(mp_years_spin)
        mp_years_row.addStretch()
        market_layout.addLayout(mp_years_row)
        bulk_pct_spin = _add_double_row(
            market_layout, "Bulk discount:", current.get('marketplace_bulk_discount_percent', 0.0), 0, 100, 1, 0, "%",
        )
        bulk_thr_row = QHBoxLayout()
        bulk_thr_row.addWidget(QLabel("Bulk discount threshold (items):"))
        bulk_thr_spin = QSpinBox()
        bulk_thr_spin.setRange(1, 100)
        bulk_thr_spin.setValue(int(current.get('marketplace_bulk_discount_threshold', 10)))
        bulk_thr_row.addWidget(bulk_thr_spin)
        bulk_thr_row.addStretch()
        market_layout.addLayout(bulk_thr_row)
        railcars_factor_spin = _add_double_row(
            market_layout,
            "Railcars per player factor:",
            current.get('marketplace_railcars_per_player_factor', 2.0),
            0.5, 10.0, 0.5, 1,
        )
        layout.addWidget(market_group)

        war_group = QGroupBox("War events")
        war_layout = QVBoxLayout(war_group)
        war_start_spin = QSpinBox()
        war_start_spin.setRange(0, 100)
        war_start_spin.setValue(int(round(float(current.get('war_start_chance_per_year', 0.0)) * 100)))
        war_start_row = QHBoxLayout()
        war_start_row.addWidget(QLabel("Start chance per year (%):"))
        war_start_row.addWidget(war_start_spin)
        war_start_row.addStretch()
        war_layout.addLayout(war_start_row)
        war_end_spin = QSpinBox()
        war_end_spin.setRange(0, 100)
        war_end_spin.setValue(int(round(float(current.get('war_end_chance_per_year', 0.0)) * 100)))
        war_end_row = QHBoxLayout()
        war_end_row.addWidget(QLabel("End chance per year (%):"))
        war_end_row.addWidget(war_end_spin)
        war_end_row.addStretch()
        war_layout.addLayout(war_end_row)
        war_draft_spin = QSpinBox()
        war_draft_spin.setRange(0, 100)
        war_draft_spin.setValue(int(round(float(current.get('war_worker_draft_chance_per_year', 0.0)) * 100)))
        war_draft_row = QHBoxLayout()
        war_draft_row.addWidget(QLabel("Worker draft chance per war year (%):"))
        war_draft_row.addWidget(war_draft_spin)
        war_draft_row.addStretch()
        war_layout.addLayout(war_draft_row)
        war_cost_spin = _add_double_row(
            war_layout, "New worker cost multiplier:", current.get('war_new_worker_cost_multiplier', 1.0), 1.0, 3.0, 0.05,
        )
        layout.addWidget(war_group)
        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        outer.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        settings = {
            'ai_debug_notifications_enabled': ai_debug_cb.isChecked(),
            'random_ai_spawn_enabled': random_spawn_cb.isChecked(),
            'random_ai_spawn_interval_years': spawn_interval_spin.value(),
            'ai_min_population': ai_min_pop_spin.value(),
            'merge_enabled': merge_enabled_cb.isChecked(),
            'merge_human_human_enabled': merge_hh_cb.isChecked(),
            'merge_ai_ai_enabled': merge_aa_cb.isChecked(),
            'merge_ai_human_enabled': merge_ah_cb.isChecked(),
            'merge_ai_healthy_profit_threshold_pct': merge_healthy_spin.value(),
            'merge_ai_reject_chance_pct': merge_reject_spin.value(),
            'merge_ai_min_turns_to_live_for_accept': merge_ttl_spin.value(),
            'merge_ai_min_years_before_consider': merge_years_spin.value(),
            'merge_ai_auto_fail_first_n_years': merge_fail_spin.value(),
            'worker_replenishment_rate': replen_spin.value(),
            'natural_disaster_frequency': disaster_spin.value(),
            'inflation_rate': inflation_spin.value(),
            'profit_margin': profit_spin.value(),
            'line_build_speed_multiplier': build_speed_spin.value(),
            'construction_cost_multiplier': construction_cost_spin.value(),
            'marketplace_years_visible': mp_years_spin.value(),
            'marketplace_bulk_discount_percent': bulk_pct_spin.value(),
            'marketplace_bulk_discount_threshold': bulk_thr_spin.value(),
            'marketplace_railcars_per_player_factor': railcars_factor_spin.value(),
            'war_start_chance_per_year': war_start_spin.value() / 100.0,
            'war_end_chance_per_year': war_end_spin.value() / 100.0,
            'war_worker_draft_chance_per_year': war_draft_spin.value() / 100.0,
            'war_new_worker_cost_multiplier': war_cost_spin.value(),
        }
        self.operations.apply_runtime_game_settings(settings)

        want_ai_watch = ai_watch_cb.isChecked() and has_ai
        if want_ai_watch and not getattr(self, 'ai_watch_enabled', False):
            self.ai_watch_enabled = True
            self._create_ai_watch_tab()
            self._update_game_session_tab_menu_actions()
        elif not want_ai_watch:
            self.ai_watch_enabled = False
            self._update_game_session_tab_menu_actions()

        if hasattr(self, '_update_propose_merge_action'):
            self._update_propose_merge_action()
        self.add_message("Game settings updated.")

    def _is_viewing_other_player(self) -> bool:
        """True when viewing another player's tab (read-only). Our player has full tabs; Other Players tab has no Map/Law/Government."""
        if hasattr(self, '_our_player_id'):
            return self.current_player_index != self._our_player_id
        return False

    def _apply_view_only_state(self):
        """When viewing another player: tabs stay enabled (can switch subtabs) but contents are read-only (no buttons)."""
        for player_id, d in self.player_tabs_dict.items():
            for key in ('assets_tab', 'map_tab', 'marketplace_tab', 'construction_tab', 'law_tab', 'government_tab'):
                w = d.get(key)
                if w is not None:
                    w.setEnabled(True)


    def _create_player_tabs(self, player, for_other_player: bool = False):
        """Create player tabs. When for_other_player=True: read-only, subtabs are Lines, Rolling Stock, Workers, Construction."""
        player_id = self.operations.get_player_id(player)
        player_name = player.get_name()
        player_tabs = QTabWidget()
        player_tabs_dict = {'for_other_player': for_other_player}
        # Assets tab: Lines, Workers, Rolling Stock combined (our player) or direct tabs (other players)
        assets_tab = QWidget()
        assets_main_layout = QVBoxLayout()
        assets_tab.setLayout(assets_main_layout)
        assets_subtabs = QTabWidget()
        assets_main_layout.addWidget(assets_subtabs)
        # Lines sub-tab
        lines_tab = QWidget()
        lines_layout = QVBoxLayout()
        lines_tab.setLayout(lines_layout)
        lines_scroll, lines_scroll_layout = _make_tab_body_scroll()
        lines_layout.addWidget(lines_scroll, 1)
        if not for_other_player:
            # No duplicate buttons at top (Extend Station, Assign Trainset, Unassign Trainset are in Edit Line popup)
            pass
        assets_subtabs.addTab(lines_tab, "Lines")
        # Workers sub-tab
        workers_tab = QWidget()
        workers_layout = QVBoxLayout()
        workers_tab.setLayout(workers_layout)
        self.add_year_display_to_tab(workers_layout, include_end_year_button=False)
        workers_scroll, workers_scroll_layout = _make_tab_body_scroll()
        workers_layout.addWidget(workers_scroll, 1)
        assets_subtabs.addTab(workers_tab, "Workers")
        # Rolling Stock sub-tabs
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
        locomotives_scroll, rolling_stock_locomotives_content_layout = _make_tab_body_scroll()
        rolling_stock_locomotives_layout.addWidget(locomotives_scroll, 1)
        rolling_stock_tabs.addTab(rolling_stock_locomotives_tab, "Locomotives")
        rolling_stock_railcars_tab = QWidget()
        rolling_stock_railcars_layout = QVBoxLayout()
        rolling_stock_railcars_tab.setLayout(rolling_stock_railcars_layout)
        railcars_scroll, rolling_stock_railcars_content_layout = _make_tab_body_scroll()
        rolling_stock_railcars_layout.addWidget(railcars_scroll, 1)
        rolling_stock_tabs.addTab(rolling_stock_railcars_tab, "Railcars")

        def on_rolling_stock_tab_changed(idx, p=player, rs_tabs=rolling_stock_tabs, rc_tab=rolling_stock_railcars_tab):
            if rs_tabs.widget(idx) == rc_tab:
                self.DisplayRailcarsInfo(p)

        rolling_stock_tabs.currentChanged.connect(on_rolling_stock_tab_changed)
        assets_subtabs.addTab(rolling_stock_tab, "Rolling Stock")
        trainsets_scroll, rolling_stock_trainsets_content_layout = _make_tab_body_scroll()
        rolling_stock_trainsets_layout.addWidget(trainsets_scroll, 1)
        if for_other_player:
            # Other players: subtabs are Lines, Rolling Stock, Workers, Construction (no Assets wrapper)
            player_tabs.addTab(lines_tab, "Lines")
            player_tabs.addTab(rolling_stock_tab, "Rolling Stock")
            player_tabs.addTab(workers_tab, "Workers")
        else:
            player_tabs.addTab(assets_tab, "Assets")
        law_tab = None
        law_layout = None
        if not for_other_player:
            law_tab = QWidget()
            law_layout = QVBoxLayout()
            law_tab.setLayout(law_layout)
            player_tabs.addTab(law_tab, "Law")
            self.add_year_display_to_tab(law_layout, include_end_year_button=False)
            # Propose Merge (request merger with desperate AI or human) - in Law tab per user request
            propose_merge_btn = QPushButton("Propose Merge")
            propose_merge_btn.setToolTip("Propose a merger with another company (AI or human). You can propose at any time. Desperate targets will accept.")
            propose_merge_btn.clicked.connect(lambda checked=False, p=player: self._on_propose_merge_clicked(p))
            law_layout.addWidget(propose_merge_btn)
            # Finance tab: profit/expense summary and AI first-5-turns debug
            finance_tab = QWidget()
            finance_layout = QVBoxLayout()
            finance_tab.setLayout(finance_layout)
            finance_label = QLabel("Finance")
            finance_label.setStyleSheet("font-weight: bold; font-size: 14px;")
            finance_layout.addWidget(finance_label)
            finance_text = QLabel("")
            finance_text.setWordWrap(True)
            finance_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            finance_scroll = QScrollArea()
            finance_scroll.setWidget(finance_text)
            finance_scroll.setWidgetResizable(True)
            finance_scroll.setMinimumHeight(120)
            finance_scroll.setMaximumHeight(500)
            finance_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            finance_layout.addWidget(finance_scroll)
            player_tabs.addTab(finance_tab, "Finance")
            # Chat input in Law tab (messages display in status bar box)
            chat_layout = QHBoxLayout()
            chat_input = QLineEdit()
            chat_input.setPlaceholderText("Type a message...")
            chat_input.setMaximumWidth(400)
            send_btn = QPushButton("Send")
            def on_send_chat():
                text = chat_input.text().strip()
                if text:
                    msg = f"{player_name}: {text}"
                    self.add_message(msg)
                    chat_input.clear()
            send_btn.clicked.connect(on_send_chat)
            chat_input.returnPressed.connect(on_send_chat)
            law_layout.addLayout(chat_layout)
            chat_layout.addWidget(chat_input)
            chat_layout.addWidget(send_btn)
            chat_layout.addStretch()
        marketplace_tab = QWidget()
        marketplace_main_layout = QVBoxLayout()
        marketplace_tab.setLayout(marketplace_main_layout)
        if not for_other_player:
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
        if not for_other_player:
            player_tabs.addTab(marketplace_tab, "Marketplace")
        map_tab = QWidget()
        # Create main horizontal layout: sidebar on left, map on right
        map_main_layout = QHBoxLayout()
        map_tab.setLayout(map_main_layout)
        
        # Create sidebar for map controls (scrollable so labels never overlap)
        map_sidebar_scroll = QScrollArea()
        map_sidebar_scroll.setFixedWidth(250)
        map_sidebar_scroll.setWidgetResizable(False)
        map_sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        map_sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        map_sidebar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        map_sidebar = QWidget()
        map_sidebar_layout = QVBoxLayout()
        map_sidebar.setLayout(map_sidebar_layout)
        map_sidebar_scroll.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        
        # Map info section
        map_info_label = QLabel("Map Info")
        map_info_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        map_sidebar_layout.addWidget(map_info_label)
        map_info_text = QLabel("")
        map_info_text.setWordWrap(True)
        map_info_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        map_info_scroll = QScrollArea()
        map_info_scroll.setWidget(map_info_text)
        map_info_scroll.setWidgetResizable(True)
        map_info_scroll.setMinimumHeight(120)
        map_info_scroll.setMaximumHeight(400)
        map_info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        map_info_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        map_sidebar_layout.addWidget(map_info_scroll)
        
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

        map_sidebar_layout.addWidget(QLabel(""))

        sell_segment_label = QLabel("Sell Line Segment")
        sell_segment_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        map_sidebar_layout.addWidget(sell_segment_label)

        sell_segment_btn = QPushButton("Sell Segment")
        map_sidebar_layout.addWidget(sell_segment_btn)

        sell_segment_combo = QComboBox()
        sell_segment_combo.addItem("Select line...")
        map_sidebar_layout.addWidget(sell_segment_combo)

        sell_segment_points_label = QLabel("Clicks: 0/2")
        map_sidebar_layout.addWidget(sell_segment_points_label)

        cancel_sell_segment_btn = QPushButton("Cancel Sell Segment")
        cancel_sell_segment_btn.setEnabled(False)
        map_sidebar_layout.addWidget(cancel_sell_segment_btn)

        map_sidebar_layout.addWidget(QLabel(""))

        sell_line_label = QLabel("Sell Entire Line")
        sell_line_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        map_sidebar_layout.addWidget(sell_line_label)

        sell_line_btn = QPushButton("Sell Line")
        map_sidebar_layout.addWidget(sell_line_btn)

        sell_line_combo = QComboBox()
        sell_line_combo.addItem("Select line...")
        map_sidebar_layout.addWidget(sell_line_combo)
        
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
        map_sidebar_layout.addStretch()  # Push everything to top
        map_sidebar.adjustSize()
        map_sidebar.setMinimumHeight(map_sidebar.sizeHint().height())
        map_sidebar_scroll.setWidget(map_sidebar)

        # Add sidebar to main layout (fixed width, no stretch)
        logger.debug("Adding sidebar to map_main_layout")
        map_main_layout.addWidget(map_sidebar_scroll, 0)  # Stretch factor 0 - fixed width
        logger.debug("Sidebar added, map_main_layout count: %d", map_main_layout.count())
        
        # Map pane: QWidget container so QWebEngineView gets stable geometry when the tab is shown.
        map_container = QWidget()
        map_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        map_container.setMinimumSize(400, 300)
        map_layout = QVBoxLayout()
        map_layout.setContentsMargins(5, 5, 5, 5)
        map_layout.setSpacing(5)
        map_container.setLayout(map_layout)
        logger.debug("map_layout created: %s", map_layout)
        map_main_layout.addWidget(map_container, 1)
        logger.debug("map_container added to map_main_layout, count: %d", map_main_layout.count())
        
        logger.debug("Adding map_tab to player_tabs")
        map_tab_index = player_tabs.addTab(map_tab, "Map") if not for_other_player else -1
        logger.debug("map_tab added, player_tabs tab count: %d", player_tabs.count())
        # Don't add year display to map tab - it covers the sidebar buttons
        
        # Add tab change handler to create QWebEngineView when map tab becomes visible
        def on_player_tab_changed(index):
            """Handle tab changes - ensure map web view is ready when Map tab becomes visible."""
            from PyQt6.QtCore import QTimer
            try:
                if index == map_tab_index:
                    def on_map_tab_shown():
                        try:
                            self._ensure_map_web_view_ready(player_id)
                            web_view = player_tabs_dict.get(f'map_web_view_{player_id}')
                            if web_view is not None:
                                web_view.setFocus()
                        except Exception as e:
                            import traceback
                            logger.debug("Error preparing map on tab change: %s", e)
                            traceback.print_exc()
                    QTimer.singleShot(50, on_map_tab_shown)
            except Exception as e:
                import traceback
                logger.debug("Error in tab change handler: %s", e)
                traceback.print_exc()
        
        player_tabs.currentChanged.connect(on_player_tab_changed)
        
        logger.debug("Storing references in player_tabs_dict")
        player_tabs_dict['map_sidebar'] = map_sidebar
        player_tabs_dict['map_sidebar_scroll'] = map_sidebar_scroll
        player_tabs_dict['map_info_text'] = map_info_text
        player_tabs_dict['map_tab_index'] = map_tab_index
        player_tabs_dict['create_line_btn'] = create_line_btn
        player_tabs_dict['line_name_input'] = line_name_input
        player_tabs_dict['line_points_label'] = line_points_label
        player_tabs_dict['map_main_layout'] = map_main_layout
        player_tabs_dict['map_tab'] = map_tab
        player_tabs_dict['map_container'] = map_container
        player_tabs_dict['map_layout'] = map_layout
        logger.debug("References stored. player_tabs_dict keys: %s", list(player_tabs_dict.keys()))
        logger.debug("map_layout stored: %s", player_tabs_dict.get('map_layout'))
        logger.debug("map_tab stored: %s", player_tabs_dict.get('map_tab'))
        logger.debug("Map tab creation complete for player %s", player_id)
        player_tabs_dict['finish_line_btn'] = finish_line_btn
        player_tabs_dict['cancel_line_btn'] = cancel_line_btn
        player_tabs_dict['extend_line_btn'] = extend_line_btn
        player_tabs_dict['extend_line_combo'] = extend_line_combo
        player_tabs_dict['extend_line_points_label'] = extend_line_points_label
        player_tabs_dict['finish_extend_btn'] = finish_extend_btn
        player_tabs_dict['cancel_extend_btn'] = cancel_extend_btn
        player_tabs_dict['sell_segment_btn'] = sell_segment_btn
        player_tabs_dict['sell_segment_combo'] = sell_segment_combo
        player_tabs_dict['sell_segment_points_label'] = sell_segment_points_label
        player_tabs_dict['cancel_sell_segment_btn'] = cancel_sell_segment_btn
        player_tabs_dict['sell_line_btn'] = sell_line_btn
        player_tabs_dict['sell_line_combo'] = sell_line_combo
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
        gov_tab = None
        gov_layout = None
        if not for_other_player:
            # Government sub-tab (per player)
            gov_tab = QWidget()
            gov_main_layout = QVBoxLayout()
            gov_tab.setLayout(gov_main_layout)
            gov_scroll = QScrollArea()
            gov_scroll.setWidgetResizable(True)
            gov_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            gov_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            gov_content = QWidget()
            gov_content.setMinimumSize(400, 450)
            gov_layout = QVBoxLayout()
            gov_content.setLayout(gov_layout)
            gov_scroll.setWidget(gov_content)
            gov_main_layout.addWidget(gov_scroll)
            player_tabs.addTab(gov_tab, "Government")

            def on_player_tab_changed(idx):
                if player_tabs.widget(idx) == gov_tab:
                    self._display_government_for_player(player, gov_layout)
                elif law_tab and player_tabs.widget(idx) == law_tab:
                    self.DisplayLawInfo(player)
                elif finance_tab and player_tabs.widget(idx) == finance_tab:
                    self.DisplayFinanceTab()

            player_tabs.currentChanged.connect(on_player_tab_changed)
            # Player subtabs closable; reopen via Corporation menu; lighter grey x for visibility
            player_tabs.setTabsClosable(True)
            if hasattr(self, '_tab_close_button_style'):
                player_tabs.setStyleSheet(self._tab_close_button_style)
            else:
                player_tabs.setStyleSheet(
                    "QTabBar::close-button { outline: none; border: none; color: #ffffff; background: transparent; min-width: 16px; min-height: 16px; } "
                    "QTabBar::close-button:hover { color: #ffffff; }"
                )
            player_tabs.tabCloseRequested.connect(
                lambda idx, pt=player_tabs: self._on_player_subtab_close_requested(pt, idx)
            )
        player_tabs_dict['government_tab'] = gov_tab
        player_tabs_dict['government_layout'] = gov_layout
        player_tabs_dict['assets_tab'] = assets_tab
        player_tabs_dict['assets_subtabs'] = assets_subtabs
        player_tabs_dict['lines_tab'] = lines_tab
        player_tabs_dict['lines_layout'] = lines_layout
        player_tabs_dict['lines_scroll'] = lines_scroll
        player_tabs_dict['lines_scroll_layout'] = lines_scroll_layout
        player_tabs_dict['rolling_stock_tab'] = rolling_stock_tab
        player_tabs_dict['rolling_stock_tabs'] = rolling_stock_tabs
        player_tabs_dict['rolling_stock_trainsets_tab'] = rolling_stock_trainsets_tab
        player_tabs_dict['rolling_stock_trainsets_layout'] = rolling_stock_trainsets_layout
        player_tabs_dict['rolling_stock_trainsets_content_layout'] = rolling_stock_trainsets_content_layout
        player_tabs_dict['rolling_stock_locomotives_tab'] = rolling_stock_locomotives_tab
        player_tabs_dict['rolling_stock_locomotives_layout'] = rolling_stock_locomotives_layout
        player_tabs_dict['rolling_stock_locomotives_content_layout'] = rolling_stock_locomotives_content_layout
        player_tabs_dict['rolling_stock_railcars_tab'] = rolling_stock_railcars_tab
        player_tabs_dict['rolling_stock_railcars_layout'] = rolling_stock_railcars_layout
        player_tabs_dict['rolling_stock_railcars_content_layout'] = rolling_stock_railcars_content_layout
        player_tabs_dict['railcars_inner_layout'] = rolling_stock_railcars_content_layout
        player_tabs_dict['workers_tab'] = workers_tab
        player_tabs_dict['workers_layout'] = workers_layout
        player_tabs_dict['workers_scroll'] = workers_scroll
        player_tabs_dict['workers_scroll_layout'] = workers_scroll_layout
        player_tabs_dict['law_tab'] = law_tab
        player_tabs_dict['law_layout'] = law_layout
        if not for_other_player:
            player_tabs_dict['propose_merge_btn'] = propose_merge_btn
            player_tabs_dict['finance_tab'] = finance_tab
            player_tabs_dict['finance_text'] = finance_text
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
        player_tabs_dict['map_container'] = map_container
        player_tabs_dict['map_layout'] = map_layout
        player_tabs_dict['construction_tab'] = construction_tab
        player_tabs_dict['construction_layout'] = construction_layout
        player_tabs_dict['construction_subtabs'] = construction_subtabs
        player_tabs_dict['in_progress_layout'] = in_progress_layout
        player_tabs_dict['complete_jobs_layout'] = complete_jobs_layout
        # Required by _open_active_player_subtab / Corporation menu (Show Map Tab, etc.)
        player_tabs_dict['player_tabs'] = player_tabs
        self.player_tabs_dict[player_id] = player_tabs_dict
        return player_tabs

    def add_message(self, msg: str):
        """Add a one-line message to all players' Messages tabs. New messages appear on top. Messages expire after 2 years."""
        if not msg or not msg.strip():
            return
        msg = msg.strip()[:200]
        if not hasattr(self, '_game_messages'):
            self._game_messages = []
        current_year = self.operations.get_year() if self.operations else 1830
        self._game_messages.insert(0, (msg, current_year))
        self._prune_old_messages()
        self._refresh_messages_display()

    def _prune_old_messages(self):
        """Remove messages older than 2 years."""
        if not self.operations:
            return
        current_year = self.operations.get_year()
        cutoff = current_year - 2
        migrated = []
        for item in getattr(self, '_game_messages', []):
            if isinstance(item, tuple):
                msg, year = item
                if year >= cutoff:
                    migrated.append((msg, year))
            else:
                migrated.append((item, current_year))
        self._game_messages = migrated

    def prune_and_refresh_messages(self):
        """Call when advancing year - prune old messages and refresh display."""
        self._prune_old_messages()
        self._refresh_messages_display()

    def _get_visible_messages(self):
        """Return messages that are still visible (within 2 years)."""
        items = getattr(self, '_game_messages', [])
        if not self.operations:
            return [m[0] if isinstance(m, tuple) else m for m in items]
        current_year = self.operations.get_year()
        cutoff = current_year - 2
        result = []
        for m in items:
            if isinstance(m, tuple):
                if m[1] >= cutoff:
                    result.append(m[0])
            else:
                result.append(m)
        return result

    def _refresh_messages_display(self):
        """Refresh message widgets with visible messages (after prune)."""
        visible = self._get_visible_messages()
        for w in getattr(self, '_message_widgets', []):
            if w is not None and hasattr(w, 'clear'):
                try:
                    w.clear()
                    for msg in visible:
                        w.addItem(QListWidgetItem(msg))
                except Exception as e:
                    logger.debug("_refresh_messages_display error: %s", e)

    def _display_government_for_player(self, player, layout):
        """Populate a layout with government chamber and controls for the given player."""
        if not self.operations or not player:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.add_year_display_to_tab(layout, include_end_year_button=False)
        seats = self.operations.get_government_seats_for_display(player)
        next_elec = self.operations.get_government_next_election_year()
        tax_mult = self.operations.get_government_tax_multiplier(player)
        layout.addWidget(QLabel(f"Next election: Year {next_elec} (every 6 years)"))
        layout.addWidget(QLabel(f"Your tax multiplier: {tax_mult:.2f}x (1.0=normal, 0.75=favorable, 1.25=unfavorable)"))
        ranking = self.operations.get_government_favor_ranking()
        ranking_parts = []
        for pid, inf in ranking:
            if inf <= 0:
                continue
            try:
                p = self.operations.get_player_by_id(pid)
                name = self.operations.get_player_name(p)
                seats_controlled = self.operations.get_player_seats_controlled(pid)
                ranking_parts.append(f"{name} (influence: {inf:.1f}, seats controlled: {seats_controlled})")
            except (KeyError, AttributeError, TypeError):
                continue
        ranking_label = QLabel("Government favor (by influence, seats controlled): " + (", ".join(ranking_parts) if ranking_parts else "No players with influence"))
        ranking_label.setWordWrap(True)
        layout.addWidget(ranking_label)
        legend_row = QWidget()
        legend_layout = QHBoxLayout(legend_row)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(4)
        for p in self.operations.get_active_players():
            color_idx = self.operations.get_player_color_index(p)
            legend_layout.addWidget(create_player_color_name_row(color_idx, self.operations.get_player_name(p)))
        legend_layout.addStretch()
        layout.addWidget(legend_row)
        layout.addWidget(QLabel("Click or right-click a seat to lobby. Right-click for allegiance/influence. Grey=no allegiance. L=leader."))
        seats_with_allegiance = self.operations.get_government_seats_with_allegiance()
        player_names = [self.operations.get_player_name(p) for p in self.operations.get_players()]
        player_color_by_id = {
            self.operations.get_player_id(p): self.operations.get_player_color_index(p)
            for p in self.operations.get_players()
        }
        chamber = GovernmentChamberWidget(
            seats,
            on_lobby_seat=lambda idx, p=player: self._open_lobby_seat_dialog(p, idx),
            on_lobby_leader=lambda p=player: self._open_lobby_leader_dialog(p),
            seats_with_allegiance=seats_with_allegiance,
            player_names=player_names,
            player_color_by_id=player_color_by_id,
        )
        layout.addWidget(chamber, 1)
        lobby_btn = QPushButton("Lobby seats (pay government)")
        lobby_btn.clicked.connect(lambda checked=False, p=player: self.on_government_lobby_clicked(p))
        layout.addWidget(lobby_btn)
        refresh_btn = QPushButton("Refresh Government")
        refresh_btn.clicked.connect(lambda checked=False, p=player: self._display_government_for_player(p, layout))
        layout.addWidget(refresh_btn)

    def DisplayGovernmentInfo(self, player=None):
        """Display government seats and lobby controls. If player is None, refreshes all players' government tabs."""
        if not self.operations:
            return
        if player is not None:
            player_id = self.operations.get_player_id(player)
            layout = self.player_tabs_dict.get(player_id, {}).get('government_layout')
            if layout:
                try:
                    _ = layout.count()
                except RuntimeError:
                    return
                self._display_government_for_player(player, layout)
        else:
            for p in self.operations.get_players():
                pid = self.operations.get_player_id(p)
                layout = self.player_tabs_dict.get(pid, {}).get('government_layout')
                if layout:
                    try:
                        _ = layout.count()
                    except RuntimeError:
                        continue
                    self._display_government_for_player(p, layout)

    def _create_ai_watch_tab(self):
        """Create AI Watch tab for investigative mode - shows AI actions during end year and AI finances (all years)."""
        if hasattr(self, 'ai_watch_text') and self.ai_watch_text:
            return  # Already created
        from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QLabel, QScrollArea
        from PyQt6.QtCore import Qt
        ai_watch_tab = QWidget()
        layout = QVBoxLayout()
        ai_watch_tab.setLayout(layout)
        # AI actions - scrollable (QTextEdit has built-in scroll bars)
        ai_actions_label = QLabel("AI Actions")
        ai_actions_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(ai_actions_label)
        self.ai_watch_text = QTextEdit()
        self.ai_watch_text.setReadOnly(True)
        self.ai_watch_text.setPlaceholderText("AI actions will appear here during End Year. Open this tab and click End Year to watch.")
        self.ai_watch_text.setMinimumHeight(120)
        self.ai_watch_text.setMaximumHeight(400)
        self.ai_watch_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.ai_watch_text, 1)
        # AI Finances (all years) — money up/down side by side
        ai_fin_label = QLabel("AI Finances (all years)")
        ai_fin_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(ai_fin_label)
        from PyQt6.QtWidgets import QHBoxLayout
        fin_row = QHBoxLayout()
        self.ai_watch_finances_down_text = QTextEdit()
        self.ai_watch_finances_down_text.setReadOnly(True)
        self.ai_watch_finances_down_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        down_scroll = QScrollArea()
        down_scroll.setWidget(self.ai_watch_finances_down_text)
        down_scroll.setWidgetResizable(True)
        down_scroll.setMinimumHeight(120)
        down_scroll.setMaximumHeight(400)
        down_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        down_label = QLabel("Money down")
        down_label.setStyleSheet("font-weight: bold;")
        down_col = QVBoxLayout()
        down_col.addWidget(down_label)
        down_col.addWidget(down_scroll)
        down_wrap = QWidget()
        down_wrap.setLayout(down_col)
        self.ai_watch_finances_up_text = QTextEdit()
        self.ai_watch_finances_up_text.setReadOnly(True)
        self.ai_watch_finances_up_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        up_scroll = QScrollArea()
        up_scroll.setWidget(self.ai_watch_finances_up_text)
        up_scroll.setWidgetResizable(True)
        up_scroll.setMinimumHeight(120)
        up_scroll.setMaximumHeight(400)
        up_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        up_label = QLabel("Money up")
        up_label.setStyleSheet("font-weight: bold;")
        up_col = QVBoxLayout()
        up_col.addWidget(up_label)
        up_col.addWidget(up_scroll)
        up_wrap = QWidget()
        up_wrap.setLayout(up_col)
        fin_row.addWidget(down_wrap, 1)
        fin_row.addWidget(up_wrap, 1)
        fin_row_widget = QWidget()
        fin_row_widget.setLayout(fin_row)
        layout.addWidget(fin_row_widget, 1)
        # Legacy alias for refresh code
        self.ai_watch_finances_text = self.ai_watch_finances_down_text
        self.tabs.addTab(ai_watch_tab, "AI Watch")

    def _on_ai_progress(self, player_name: str, description: str):
        """Append AI action (detailed description) to the AI Watch tab."""
        if hasattr(self, 'ai_watch_text') and self.ai_watch_text:
            self.ai_watch_text.append(f"{player_name}: {description}")
        if description.startswith(("Built line", "Extended line", "Bought line")):
            self._schedule_ai_map_refresh()

    def _schedule_ai_map_refresh(self):
        """Debounced map refresh so AI-built lines appear during the year, not only at year-end."""
        if not hasattr(self, '_ai_map_refresh_timer'):
            self._ai_map_refresh_timer = QTimer(self)
            self._ai_map_refresh_timer.setSingleShot(True)
            self._ai_map_refresh_timer.timeout.connect(self._refresh_map_after_ai_action)
        self._ai_map_refresh_timer.start(300)

    def _refresh_map_after_ai_action(self):
        """Lightweight map update only — never reload HTML (preserves in-progress line creation)."""
        if not self.operations:
            return
        try:
            game_map = self.operations.get_map()
            if game_map and hasattr(self, '_build_map_overview_text'):
                self._update_map_sidebars(self._build_map_overview_text(game_map))
            if hasattr(self, '_refresh_map_overlays'):
                self._refresh_map_overlays()
        except Exception as e:
            logger.debug("AI map refresh failed: %s", e)
        if hasattr(self, 'refresh_construction_tab'):
            self.refresh_construction_tab()

    def _any_map_edit_in_progress(self) -> bool:
        """True when a human is placing line/extension points (map HTML must not reload)."""
        tabs = getattr(self, 'player_tabs_dict', None) or {}
        for player_tabs in tabs.values():
            if not isinstance(player_tabs, dict):
                continue
            if player_tabs.get('line_creation_active') or player_tabs.get('line_extension_active'):
                return True
            if player_tabs.get('line_segment_sell_active'):
                return True
            if player_tabs.get('line_creation_finishing') or player_tabs.get('line_extension_finishing'):
                return True
        return False

    def _set_line_creation_active(self, player_tabs_dict: dict, active: bool) -> None:
        if player_tabs_dict is not None:
            player_tabs_dict['line_creation_active'] = bool(active)
            if not active:
                player_tabs_dict['line_creation_finishing'] = False

    def _set_line_extension_active(self, player_tabs_dict: dict, active: bool) -> None:
        if player_tabs_dict is not None:
            player_tabs_dict['line_extension_active'] = bool(active)
            if not active:
                player_tabs_dict['line_extension_finishing'] = False

    def _set_line_segment_sell_active(self, player_tabs_dict: dict, active: bool) -> None:
        if player_tabs_dict is not None:
            player_tabs_dict['line_segment_sell_active'] = bool(active)
            if not active:
                player_tabs_dict['line_segment_sell_finishing'] = False

    def _create_news_log_tab(self):
        """Create a News Log tab with generated yearly newspaper-style articles."""
        if hasattr(self, 'news_log_browser') and self.news_log_browser:
            return
        news_tab = QWidget()
        news_layout = QVBoxLayout()
        news_tab.setLayout(news_layout)
        self.news_log_browser = QTextBrowser()
        self.news_log_browser.setReadOnly(True)
        self.news_log_browser.setOpenExternalLinks(True)
        self.news_log_browser.setPlaceholderText("Generated news articles will appear here after each year.")
        news_layout.addWidget(self.news_log_browser)
        self.tabs.addTab(news_tab, "News Log")
        self._refresh_news_log_tab()

    def _refresh_news_log_tab(self):
        """Refresh News Log tab from operations-managed article log."""
        if not hasattr(self, 'news_log_browser') or self.news_log_browser is None:
            return
        if not self.operations or not hasattr(self.operations, 'get_news_log'):
            self.news_log_browser.setPlainText("News log unavailable.")
            return
        items = self.operations.get_news_log(limit=250) or []
        if not items:
            self.news_log_browser.setPlainText("No articles yet. Advance a year to generate transit news.")
            return
        blocks = []
        for a in items:
            year = a.get('year', '')
            category = a.get('category', 'General')
            headline = a.get('headline', 'Untitled')
            body = a.get('body', '')
            blocks.append(
                f"<h3>{headline}</h3>"
                f"<p><i>{year} · {category}</i></p>"
                f"<p>{body}</p><hr/>"
            )
        self.news_log_browser.setHtml("".join(blocks))

    def _create_rules_tab(self):
        """Create a Rules tab: sidebar categories + stacked text for each ## section of RULES.md."""
        rules_tab = QWidget()
        rules_layout = QVBoxLayout()
        rules_tab.setLayout(rules_layout)

        from ruleset_loader import get_active_ruleset, read_rules_markdown
        rules_content = read_rules_markdown(get_active_ruleset())
        if self.operations and hasattr(self.operations, 'ruleset_name'):
            rules_content = read_rules_markdown(self.operations.ruleset_name)

        sections = _parse_rules_markdown_sections(rules_content)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_w = QListWidget()
        list_w.setMinimumWidth(160)
        list_w.setMaximumWidth(280)
        stack = QStackedWidget()
        for title, body in sections:
            browser = QTextBrowser()
            browser.setReadOnly(True)
            browser.setPlainText(body if body else '(No content in this section.)')
            stack.addWidget(browser)
            list_w.addItem(title)

        def on_section_changed(row: int):
            if 0 <= row < stack.count():
                stack.setCurrentIndex(row)

        list_w.currentRowChanged.connect(on_section_changed)
        if list_w.count() > 0:
            list_w.setCurrentRow(0)
        splitter.addWidget(list_w)
        splitter.addWidget(stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        rules_layout.addWidget(splitter)
        rules_btn_row = QHBoxLayout()
        edit_rules_btn = QPushButton("Edit Rules (Ruleset Editor)")
        edit_rules_btn.clicked.connect(self._open_ruleset_editor_rules_tab)
        refresh_rules_btn = QPushButton("Refresh")
        refresh_rules_btn.clicked.connect(self._refresh_rules_tab_content)
        rules_btn_row.addWidget(edit_rules_btn)
        rules_btn_row.addWidget(refresh_rules_btn)
        rules_btn_row.addStretch()
        rules_layout.addLayout(rules_btn_row)
        self._rules_tab_list = list_w
        self._rules_tab_stack = stack
        self.tabs.addTab(rules_tab, "Rules")
    
    def _create_vehicles_tab(self):
        """Create a Vehicles tab that displays a README file with vehicle details"""
        vehicles_tab = QWidget()
        vehicles_layout = QVBoxLayout()
        vehicles_tab.setLayout(vehicles_layout)
        
        vehicles_browser = QTextBrowser()
        vehicles_browser.setReadOnly(True)
        from ruleset_loader import get_active_ruleset, read_vehicles_markdown
        rs_name = getattr(self.operations, 'ruleset_name', None) if self.operations else None
        vehicles_browser.setPlainText(read_vehicles_markdown(rs_name or get_active_ruleset()))
        vehicles_layout.addWidget(vehicles_browser)
        vehicles_btn_row = QHBoxLayout()
        edit_vehicles_btn = QPushButton("Edit Vehicles (Ruleset Editor)")
        edit_vehicles_btn.clicked.connect(self._open_ruleset_editor_vehicles_tab)
        refresh_vehicles_btn = QPushButton("Refresh")
        refresh_vehicles_btn.clicked.connect(self._refresh_vehicles_tab_content)
        vehicles_btn_row.addWidget(edit_vehicles_btn)
        vehicles_btn_row.addWidget(refresh_vehicles_btn)
        vehicles_btn_row.addStretch()
        vehicles_layout.addLayout(vehicles_btn_row)
        self._vehicles_tab_browser = vehicles_browser
        self.tabs.addTab(vehicles_tab, "Vehicles")

    def _refresh_rules_tab_content(self):
        if not hasattr(self, '_rules_tab_list') or not hasattr(self, '_rules_tab_stack'):
            return
        from ruleset_loader import get_active_ruleset, read_rules_markdown
        rs = getattr(self.operations, 'ruleset_name', None) if self.operations else None
        sections = _parse_rules_markdown_sections(read_rules_markdown(rs or get_active_ruleset()))
        self._rules_tab_list.clear()
        while self._rules_tab_stack.count():
            w = self._rules_tab_stack.widget(0)
            self._rules_tab_stack.removeWidget(w)
            w.deleteLater()
        for title, body in sections:
            browser = QTextBrowser()
            browser.setReadOnly(True)
            browser.setPlainText(body if body else '(No content in this section.)')
            self._rules_tab_stack.addWidget(browser)
            self._rules_tab_list.addItem(title)
        if self._rules_tab_list.count() > 0:
            self._rules_tab_list.setCurrentRow(0)

    def _refresh_vehicles_tab_content(self):
        if not hasattr(self, '_vehicles_tab_browser'):
            return
        from ruleset_loader import get_active_ruleset, read_vehicles_markdown
        rs = getattr(self.operations, 'ruleset_name', None) if self.operations else None
        self._vehicles_tab_browser.setPlainText(read_vehicles_markdown(rs or get_active_ruleset()))

    def _on_game_settings_ruleset_changed(self, name: str):
        if not name:
            return
        from ruleset_loader import set_active_ruleset, activate_vehicle_registry_for_ruleset
        try:
            set_active_ruleset(name)
            activate_vehicle_registry_for_ruleset(name)
        except Exception:
            pass

    def _open_ruleset_editor_from_settings(self):
        dlg = RulesetEditorDialog(self)
        if dlg.exec():
            from ruleset_loader import get_active_ruleset, list_rulesets
            if hasattr(self, 'game_ruleset_combo'):
                active = get_active_ruleset()
                self.game_ruleset_combo.blockSignals(True)
                self.game_ruleset_combo.clear()
                for rs_name in list_rulesets():
                    self.game_ruleset_combo.addItem(rs_name)
                idx = self.game_ruleset_combo.findText(active)
                if idx >= 0:
                    self.game_ruleset_combo.setCurrentIndex(idx)
                self.game_ruleset_combo.blockSignals(False)

    def _open_ruleset_editor_rules_tab(self):
        dlg = RulesetEditorDialog(self, initial_tab=0)
        dlg.exec()
        self._refresh_rules_tab_content()

    def _open_ruleset_editor_vehicles_tab(self):
        dlg = RulesetEditorDialog(self, initial_tab=1)
        dlg.exec()
        self._refresh_vehicles_tab_content()

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
                        mait = self.operations.calc_line_maintenance(line, current_year)
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
                tax_str = f"    - Taxes: poll=${finances['poll_tax']}, property=${finances['property_tax']}"
                if finances.get('income_tax', 0):
                    tax_str += f", income=${finances['income_tax']}"
                out.append(tax_str)
                if finances['worker_costs'] > 0:
                    out.append(f"    - Worker costs: ${int(finances['worker_costs'])}")
                out.append(f"    - Net this year: ${int(finances['net'])}")
            except Exception as e:
                out.append(f"  Financial Summary: Error calculating finances: {str(e)}")

        QMessageBox.information(self, "Player Info", "\n".join(out))

    def _ensure_correct_player(self):
        """Ensure current_player_index is set to assigned player in networked mode."""
        if self.is_network_mode:
            self.current_player_index = self.assigned_player_id

    def on_player_changed(self):
        if self.is_network_mode:
            self.current_player_index = self.assigned_player_id
            return
        selected_name = self.player_combo.currentText()
        for idx, p in enumerate(self.operations.get_players()):
            if self.operations.get_player_name(p) == selected_name and not self.operations.is_player_eliminated(p):
                self.current_player_index = idx
                self.operations.set_current_player_index(idx)
                break
    
    def check_and_eliminate_player(self, player, balance_before=None, net_change=None):
        was_eliminated = self.operations.check_and_eliminate_player(
            player, balance_before=balance_before, net_change=net_change,
        )
            
        if was_eliminated:
            player_name = self.operations.get_player_name(player)
            self.add_message(f"{player_name} eliminated (bankrupt).")
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
            pt = point_combo.currentData()
            if pt is None:
                total_cost = self.operations.calculate_station_cost(1 + selected_count)
            else:
                px, py = pt
                total_cost = self.operations.calculate_station_cost(
                    1 + selected_count, px, py,
                    player=current_player, station_name=name_input,
                )
            merge_hint = ""
            if pt is not None:
                target = self.operations.find_player_station_merge_target(
                    current_player, name_input, pt[0], pt[1],
                )
                if target is not None:
                    merge_hint = " (merge extension — discounted)"
            cost_label.setText(
                f"Total cost: ${total_cost}{merge_hint} (Base: $500 + $250 per additional line)"
            )
        
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
                self.update_status_bar()
                dlg.close()
                self.refresh_construction_tab()
                self.DisplayMapInfo()
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

    def on_upgrade_line_for_line(self, line, player):
        """Open upgrade dialog with this line pre-selected."""
        line_name = getattr(line, 'name', None)
        if not line_name:
            return
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player or self.operations.get_player_id(current_player) != self.operations.get_player_id(player):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Upgrade Line")
        dlg.setGeometry(200, 200, 400, 200)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Upgrade: {line_name}"))
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
            upgrades = []
            if overhead_chk.isChecked():
                upgrades.append('overhead')
            if thirdrail_chk.isChecked():
                upgrades.append('thirdrail')
            if not upgrades:
                QMessageBox.warning(dlg, "Error", "Select at least one upgrade.")
                return
            try:
                result = self.operations.upgrade_line_complete(line_name, current_player, upgrades)
                self.check_and_eliminate_player(current_player)
                QMessageBox.information(self, "Upgraded",
                    f"Upgraded {result['line_name']}. Cost: ${result['cost']}. Remaining money: ${result['remaining_money']}")
                dlg.close()
                self.DisplayLinesControls(player)
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))

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

        layout.addWidget(QLabel("Enter new points (x, y) - one per line:"))
        layout.addWidget(QLabel("The first point attaches to whichever line end is closer; each next point connects to the prior point."))
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

            extend_from_end = self.operations.extension_attach_from_closest_end(
                selected_line_name, new_points[0],
            )

            segment_specs = self.operations.get_extension_elevation_specs(
                selected_line_name, new_points, extend_from_end,
            )
            elevation_levels = self._prompt_route_elevation_levels(
                segment_specs, parent=dlg,
            )
            if elevation_levels is None:
                return
            
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
                result = self.operations.extend_line_complete(
                    selected_line_name, current_player, new_points, extend_from_end,
                    elevation_levels, cost_info=cost_info,
                )
                self.check_and_eliminate_player(current_player)
                self.add_message(f"Line '{result['line_name']}' extension started.")
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

            segment_specs = self.operations.get_line_creation_elevation_specs(points)
            elevation_levels = self._prompt_route_elevation_levels(
                segment_specs, parent=points_dialog,
            )
            if elevation_levels is None:
                return

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
                if result and result.get('success') is False:
                    QMessageBox.warning(self, "Cannot Create Line", result.get('error_message', 'Unknown error'))
                    return
                self.check_and_eliminate_player(current_player)
                self.add_message(f"Line '{line_name}' construction started.")
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
                    effective = self.operations.get_effective_player(player)
                    money = self.operations.get_player_money(effective)
                    name = self.operations.get_player_name(effective)
                    if effective is not player:
                        name = f"{self.operations.get_player_name(player)} (with {name})"
                    entry = self.status_money_labels[i]
                    color_idx = self.operations.get_player_color_index(player)
                    set_status_player_money_display(
                        entry['swatch'], entry['text'], color_idx, name, money,
                        row=entry.get('widget'),
                    )
            for i in range(len(active_players), len(self.status_money_labels)):
                if i in self.status_money_labels:
                    entry = self.status_money_labels[i]
                    set_status_player_money_display(
                        entry['swatch'], entry['text'], 0, "", "",
                        row=entry.get('widget'),
                    )
            self._compact_status_bar_height()
        except Exception:
            pass

    def _prompt_route_elevation_levels(
        self,
        segment_specs: list,
        *,
        is_bus_line: bool = False,
        parent=None,
        title: str = "Select Elevation Levels",
    ):
        """Prompt for one elevation per spec. Returns list or None if cancelled."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QComboBox, QPushButton, QScrollArea, QWidget
        from operations import _LAND_SEGMENT_ELEVATIONS

        if not segment_specs:
            return []
        dlg_parent = parent if parent is not None else self
        elev_dlg = QDialog(dlg_parent)
        elev_dlg.setWindowTitle(title)
        elev_dlg.setGeometry(300, 300, 400, min(600, 180 + len(segment_specs) * 50))
        elev_layout = QVBoxLayout()
        elev_layout.addWidget(QLabel(f"Select elevation for each of {len(segment_specs)} segment(s):"))
        elev_layout.addWidget(QLabel("Over-water segments: above-ground or underground only (default above-ground)."))
        all_row = QHBoxLayout()
        all_row.addWidget(QLabel("Apply to all:"))
        all_combo = QComboBox()
        all_combo.addItems(list(_LAND_SEGMENT_ELEVATIONS))
        all_combo.setCurrentText('street')
        all_row.addWidget(all_combo)
        elevation_combos = []

        def apply_all():
            choice = all_combo.currentText()
            for combo in elevation_combos:
                idx = combo.findText(choice)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(apply_all)
        all_row.addWidget(apply_btn)
        all_row.addStretch()
        elev_layout.addLayout(all_row)

        elev_scroll = QScrollArea()
        elev_scroll.setWidgetResizable(True)
        elev_widget = QWidget()
        elev_widget_layout = QVBoxLayout()
        elev_widget.setLayout(elev_widget_layout)
        elev_scroll.setWidget(elev_widget)
        elev_layout.addWidget(elev_scroll)

        for spec in segment_specs:
            elev_widget_layout.addWidget(QLabel(spec['label']))
            choices, default = self.operations.get_segment_elevation_options(
                spec['p1'], spec['p2'], is_bus_line=is_bus_line,
            )
            combo = QComboBox()
            combo.addItems(choices)
            combo.setCurrentText(default if default in choices else choices[0])
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
            return None
        return [combo.currentText() for combo in elevation_combos]

    def refresh_all_displays(self):
        """Refresh all UI displays. Uses assigned_player for government when in network mode."""
        if not self.operations:
            return
        if hasattr(self, '_update_propose_merge_action'):
            self._update_propose_merge_action()
        if hasattr(self, 'DisplayMapInfo'):
            self.DisplayMapInfo()
        if hasattr(self, 'DisplayMarketplaceInfo'):
            self.DisplayMarketplaceInfo()
        if hasattr(self, 'refresh_construction_tab'):
            self.refresh_construction_tab()
        if hasattr(self, 'update_status_bar'):
            self.update_status_bar()
        if hasattr(self, 'DisplayGovernmentInfo'):
            self.DisplayGovernmentInfo()  # Refreshes all players' government tabs
        if hasattr(self, 'DisplayFinanceTab'):
            self.DisplayFinanceTab()
        if hasattr(self, 'DisplayAIWatchFinances'):
            self.DisplayAIWatchFinances()
        for player in self.operations.get_players():
            if hasattr(self, 'DisplayLinesControls'):
                self.DisplayLinesControls(player)
            if hasattr(self, 'DisplayRollingStockInfo'):
                self.DisplayRollingStockInfo(player)
            if hasattr(self, 'DisplayWorkersControls'):
                self.DisplayWorkersControls(player)
            if hasattr(self, 'DisplayLawInfo'):
                try:
                    self.DisplayLawInfo(player)
                except Exception:
                    pass

    def advance_year_button_clicked(self):
        if self.is_network_mode and self.network_client:
            try:
                self.network_client.end_year()
            except Exception as e:
                QMessageBox.warning(self, "Network Error", f"Failed to send end year request: {str(e)}")
                import traceback
                traceback.print_exc()
            return
        if getattr(self, '_advance_year_in_progress', False):
            return
        self._request_year_end()

    def _launch_advance_year_thread(self):
        """End-year sequence: any remaining AI turns, then advance_year_complete when all players are done."""
        if getattr(self, '_advance_year_in_progress', False):
            return
        self._advance_year_in_progress = True
        import time
        old_year = self.operations.get_year()
        if self.status_end_year_btn:
            self.status_end_year_btn.setEnabled(False)
        pending_ai = len(self.operations.get_ai_players_needing_in_year_turn())
        num_ai = sum(1 for p in self.operations.get_players()
                     if getattr(p, 'is_ai', False) and not self.operations.is_player_eliminated(p))
        last_elapsed = getattr(self, '_last_advance_year_elapsed_sec', None)
        last_num_ai = getattr(self, '_last_advance_year_num_ai', None)
        if pending_ai == 0:
            self._advance_year_estimate_sec = 5
        elif last_elapsed is not None and last_num_ai == num_ai:
            scale = pending_ai / max(1, num_ai)
            self._advance_year_estimate_sec = max(5, int(last_elapsed * scale * 1.15) + 1)
        else:
            self._advance_year_estimate_sec = max(5, pending_ai * 10 + 8)
        self._advance_year_start_time = time.time()
        if self.status_end_year_timer_label:
            self.status_end_year_timer_label.setVisible(True)
            self._update_end_year_timer_estimate()
        self._advance_year_timer = QTimer(self)
        self._advance_year_timer.timeout.connect(self._update_end_year_timer_estimate)
        self._advance_year_timer.start(1000)
        self._advance_year_thread = AdvanceYearThread(self.operations, old_year)
        self._advance_year_thread.finished_with_result.connect(self._on_advance_year_worker_finished)
        self._advance_year_thread.ai_progress.connect(self._on_ai_progress)
        if hasattr(self, 'ai_watch_text') and self.ai_watch_text:
            self.ai_watch_text.clear()
            self.ai_watch_text.append("End Year started - AI actions:")
        self._advance_year_thread.start()

    def _run_advance_year_sync(self, old_year):
        """Run remaining AI turns and advance_year_complete. Returns (result, ai_results_list, error_occurred, error_message)."""
        from ai import (
            run_ai_turn,
            AIIdleTurnError,
            validate_ai_turn_results,
            run_pending_ai_in_year_turns,
            AI_TURN_TIME_BUDGET_SEC,
        )
        app = QApplication.instance()
        pending = self.operations.get_ai_players_needing_in_year_turn()
        ai_results_list, _ = run_pending_ai_in_year_turns(
            self.operations,
            players=pending,
            deadline_sec=AI_TURN_TIME_BUDGET_SEC,
        )
        if app:
            app.processEvents()
        if not self.operations.all_players_completed_year_actions():
            return (None, ai_results_list, True, "Cannot advance year until every player has finished.")
        try:
            result = self.operations.advance_year_complete(old_year)
            if app:
                app.processEvents()
            # AIs that spawn after turn 1 get to take actions in the same round
            if result and result.get('new_ai_spawned'):
                new_info = result['new_ai_spawned']
                new_player = new_info.get('player') if isinstance(new_info, dict) else None
                if new_player is not None:
                    try:
                        acts = run_ai_turn(self.operations, new_player)
                        validate_ai_turn_results(self.operations, new_player, acts)
                        self.operations.mark_ai_in_year_turn_completed(new_player)
                        ai_results_list.append((new_player, acts))
                    except AIIdleTurnError as e:
                        ai_results_list.append((new_player, []))
                    except Exception as e:
                        self.operations.mark_ai_in_year_turn_completed(new_player)
                        ai_results_list.append((new_player, []))
                    if app:
                        app.processEvents()
            return (result, ai_results_list, False, "")
        except Exception as e:
            return (None, ai_results_list, True, str(e))

    def _update_end_year_timer_estimate(self):
        """Update the end year timer label every second. Estimate adapts when taking longer than expected."""
        if not getattr(self, '_advance_year_start_time', None) or not self.status_end_year_timer_label:
            return
        import time
        elapsed = int(time.time() - self._advance_year_start_time)
        estimate = getattr(self, '_advance_year_estimate_sec', 10)
        # When elapsed exceeds estimate, extend modestly (avoids "~10s left" when 1s from done)
        if elapsed >= estimate:
            last_elapsed = getattr(self, '_last_advance_year_elapsed_sec', None)
            if last_elapsed is not None and last_elapsed > elapsed:
                # Use historical: assume similar proportion remaining
                remaining_from_last = last_elapsed - elapsed
                self._advance_year_estimate_sec = elapsed + max(2, int(remaining_from_last))
            else:
                self._advance_year_estimate_sec = elapsed + 3
            estimate = self._advance_year_estimate_sec
        remaining = max(0, estimate - elapsed)
        self.status_end_year_timer_label.setText(f"~{remaining}s left (elapsed: {elapsed}s)")
        self.status_end_year_timer_label.setToolTip("Estimated time until end year cycle completes. Updates every second.")

    def _on_advance_year_worker_finished(self, result, ai_results_list, error_occurred, error_message, ai_build_failures=None):
        if hasattr(self, '_advance_year_timer') and self._advance_year_timer:
            self._advance_year_timer.stop()
            self._advance_year_timer = None
        if self.status_end_year_timer_label:
            self.status_end_year_timer_label.setVisible(False)
            self.status_end_year_timer_label.setText("")
        # Store actual elapsed for next cycle's estimate (more accurate over time)
        if getattr(self, '_advance_year_start_time', None):
            import time
            elapsed = time.time() - self._advance_year_start_time
            num_ai = sum(1 for p in self.operations.get_players()
                         if getattr(p, 'is_ai', False) and not self.operations.is_player_eliminated(p))
            self._last_advance_year_elapsed_sec = elapsed
            self._last_advance_year_num_ai = num_ai
        self._advance_year_in_progress = False
        self._year_end_pending = False
        if self.status_end_year_btn:
            self.status_end_year_btn.setEnabled(True)
        self._update_year_end_waiting_ui()
        ai_build_failures = ai_build_failures or []
        if error_occurred:
            from PyQt6.QtWidgets import QMessageBox
            msg = f"Error during year advance: {error_message}"
            if ai_build_failures:
                msg += "\n\nAI build failures:\n" + "\n".join(f"• {p}: {e}" for p, e in ai_build_failures)
            QMessageBox.critical(self, "Error", msg)
            return
        if ai_build_failures:
            self._show_ai_build_failure_popup(ai_build_failures)
        self._apply_advance_year_result(result, ai_results_list)

    def _show_ai_build_failure_popup(self, ai_build_failures: list):
        """Show chat message and popup for AI build failures and debug issues."""
        from PyQt6.QtWidgets import QMessageBox
        lines = []
        for player_name, err in ai_build_failures:
            lines.append(f"• {player_name}: {err}")
            self.add_message(f"AI issue ({player_name}): {err}")
        msg = (
            "AI reported one or more issues this turn.\n\n"
            + "\n".join(lines)
            + "\n\n"
            "On OSM maps, lines must follow street polylines. "
            "Disable AI debug notifications in Game → AI settings if these alerts are not needed."
        )
        QMessageBox.warning(self, "AI Issue", msg)

    def _apply_advance_year_result(self, result, ai_results_list):
        """Apply advance year result: update UI, show dialogs. AI actions are not shown in messages."""
        if result is None:
            return
        from PyQt6.QtWidgets import QMessageBox
        new_year = result['new_year']
        self.add_message(f"Year {new_year}.")
        if self.status_year_label:
            self.status_year_label.setText(f"Year: {new_year}")

        ai_auto_advancing = self._is_current_player_ai()

        if result['strike_warnings']:
            strike_warnings = [w['message'] for w in result['strike_warnings']]
            self.add_message("Strike warning: " + "; ".join(strike_warnings))
            if not ai_auto_advancing:
                QMessageBox.warning(self, "Strike Warning", "⚠️ WORKERS THREATENING TO STRIKE ⚠️\n\n" + "\n\n".join(strike_warnings))

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
            
            self.add_message(f"Natural disaster: {disaster_info['name']}.")
            # AI Watch: show when an AI was negatively affected
            if damaged_lines or damaged_stations:
                map_lines = self.operations.get_map_lines()
                ai_affected = set()
                for line_name in damaged_lines:
                    line = map_lines.get(line_name)
                    if line:
                        owner = self.operations.get_line_owner(line)
                        if owner and getattr(owner, 'is_ai', False):
                            ai_affected.add(owner.get_name())
                if ai_affected and hasattr(self, 'ai_watch_text') and self.ai_watch_text:
                    for pname in ai_affected:
                        lines_str = ', '.join(damaged_lines[:3]) + ('...' if len(damaged_lines) > 3 else '')
                        self.ai_watch_text.append(f"{pname} (AI): Natural disaster damaged lines: {lines_str}")
            if not ai_auto_advancing:
                QMessageBox.warning(self, "Natural Disaster", msg)

        war_ev = result.get('war_events') or {}
        if war_ev.get('war_started'):
            self.add_message("War declared: new worker hires cost more until the conflict ends.")
            if not ai_auto_advancing:
                QMessageBox.warning(
                    self, "War Declared",
                    "A major conflict has begun.\n\n"
                    "Hiring additional workers is more expensive while the war lasts. "
                    "Draft events can remove workers; companies with less government favor are hit harder.",
                )
        if war_ev.get('war_ended'):
            self.add_message("War ended: labor pressure on new hires eases.")
            if not ai_auto_advancing:
                QMessageBox.information(self, "War Ended", "The conflict has ended. Worker hire costs return to normal.")
        draft_lines = []
        for d in war_ev.get('drafts', []) or []:
            pname = d.get('player_name', 'Operator')
            n = int(d.get('count', 0) or 0)
            if n > 0:
                self.add_message(f"Draft call-up: {pname} lost {n} worker(s).")
                draft_lines.append(f"• {pname}: lost {n} worker(s)")
        if draft_lines and not ai_auto_advancing:
            QMessageBox.information(
                self, "Worker Draft",
                "Wartime manpower demands affected operators:\n\n" + "\n".join(draft_lines),
            )

        if result['lawsuit_result']:
            lawsuit = result['lawsuit_result']
            outcome = "agency won" if lawsuit['agency_won'] else f"{lawsuit['player']} won"
            self.add_message(f"Lawsuit: {lawsuit['agency']} vs {lawsuit['player']} - {outcome}.")
            if not ai_auto_advancing:
                if lawsuit['agency_won']:
                    QMessageBox.warning(self, "Agency Lawsuit", f"{lawsuit['agency']} sued {lawsuit['player']} for {lawsuit['excuse']}! The agency won. {lawsuit['player']} lost ${lawsuit['penalty']}.")
                else:
                    QMessageBox.information(self, "Agency Lawsuit", f"{lawsuit['agency']} sued {lawsuit['player']} for {lawsuit['excuse']}. {lawsuit['player']} won.")

        for event in result.get('newly_triggered_events', []):
            self.add_message(f"Event: {event.name} ({event.year}).")
        if result.get('newly_triggered_events') and not ai_auto_advancing:
            msg = "\n\n".join(f"• {e.name} ({e.year})\n{e.description}" for e in result['newly_triggered_events'])
            QMessageBox.information(self, "Historical Event", msg)

        for name, _ in sorted(result.get('newly_available_vehicles', []), key=lambda x: x[1]):
            self.add_message(f"New vehicle: {name}.")
        if result.get('newly_available_vehicles') and not ai_auto_advancing:
            msg = "\n".join(f"• {n} (discovered {y})" for n, y in sorted(result['newly_available_vehicles'], key=lambda x: x[1]))
            QMessageBox.information(self, "Technology Breakthrough", msg)

        if result.get('new_ai_spawned'):
            spawn_info = result['new_ai_spawned']
            name = spawn_info.get('name', 'New Transit Co')
            self.add_message(f"A new company, {name}, has entered the market!")
            if not ai_auto_advancing:
                QMessageBox.information(self, "New Competitor", f"A new transit company, {name}, has entered the market with ${spawn_info.get('money', 0):,} in capital.")

        # Show last turn's headlines in the message feed, then this turn's generated headlines.
        prev_year = int(new_year) - 1
        try:
            prev_items = self.operations.get_news_log(limit=250) if hasattr(self.operations, 'get_news_log') else []
        except Exception:
            prev_items = []
        prev_headlines = []
        for a in (prev_items or []):
            ay = a.get('year')
            if ay == prev_year:
                prev_headlines.append(a.get('headline', 'News'))
        if prev_headlines:
            self.add_message(f"News recap ({prev_year}): {prev_headlines[0]}")

        # Generated newspaper-style news (mostly tied to events/economy this year)
        for article in result.get('news_articles', []) or []:
            headline = article.get('headline', 'News')
            year = article.get('year', new_year)
            self.add_message(f"News ({year}): {headline}")
        self._refresh_news_log_tab()

        for acquirer, target in result.get('ai_ai_merges', []):
            if self.operations.is_player_eliminated(acquirer) or self.operations.is_player_eliminated(target):
                continue
            acquirer_name = self.operations.get_player_name(acquirer)
            target_name = self.operations.get_player_name(target)
            self.add_message(f"{acquirer_name} merged with {target_name}. {target_name} has been absorbed.")

        for notif in result.get('lobby_seat_notifications', []):
            prev_owner_id = notif.get('previous_owner_id')
            lobbyist_name = notif.get('lobbyist_name', 'Unknown')
            prev_owner_name = self.operations.get_player_name(next((p for p in self.operations.get_players() if self.operations.get_player_id(p) == prev_owner_id), None)) if prev_owner_id is not None else 'Unknown'
            if notif.get('type') == 'tiebreaker':
                msg = f"{lobbyist_name} lobbied the tiebreaker (was favorable to {prev_owner_name}). They succeeded."
            else:
                seat_idx = notif.get('seat_index', '?')
                msg = f"{lobbyist_name} lobbied seat {seat_idx} (was favorable to {prev_owner_name}). They succeeded."
            self.add_message(msg)

        for ai_player, human_player in result.get('merge_proposals', []):
            if self.operations.is_player_eliminated(ai_player) or self.operations.is_player_eliminated(human_player):
                continue
            ai_name = self.operations.get_player_name(ai_player)
            human_name = self.operations.get_player_name(human_player)
            reply = QMessageBox.question(
                self, "Merge Proposal",
                f"{ai_name} is in desperate financial straits and offers to merge with you.\n\n"
                f"You would acquire all of their lines, trainsets, workers, and remaining funds.\n"
                f"{ai_name} would be eliminated from the game.\n\n"
                f"Accept the merger?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                merge_result = self.operations.merge_players(human_player, ai_player)
                if merge_result.get('success'):
                    self.add_message(f"{human_name} merged with {ai_name}. {ai_name} has been absorbed.")
                    QMessageBox.information(self, "Merger Complete", f"You have merged with {ai_name}. All their assets are now yours.")

        from worker import LAWYER
        import random
        old_year = new_year - 1
        if self.last_lawyer_replenishment_year is None:
            self.last_lawyer_replenishment_year = old_year - random.randint(0, 4)
        years_since_last_lawyer = new_year - self.last_lawyer_replenishment_year
        if years_since_last_lawyer >= 5:
            lawyer_replenishment = random.randint(1, 3)
            self.operations.marketplace_add_worker_supply(LAWYER.name, lawyer_replenishment)
            self.last_lawyer_replenishment_year = new_year
        
        profit_info = "\n---Profit---\n"
        maintenance_info = "\n---Trainset Maintenance---\n"
        resource_costs_info = "\n---Resource Costs---\n"
        line_maintenance_info = "\n---Line Maintenance---\n"
        tax_info = "\n---Taxes---\n"
        net_info = "\n---Net Change---\n"

        for pf in result['player_finances']:
            player = pf['player']
            if self.operations.is_player_eliminated(player):
                continue  # Skip dead players in end of year summary
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
            tax_str = f"{self.operations.get_player_name(player)}: poll=${finances['poll_tax']}, property=${finances['property_tax']}"
            if finances.get('income_tax', 0):
                tax_str += f", income=${finances['income_tax']}"
            tax_info += tax_str + "\n"
            grant = finances.get('government_grant', 0)
            if grant:
                tax_info += f"  (Gov grant: +${grant})\n"
            net_val = finances['net']
            net_sign = "+" if net_val > 0 else ""
            net_info += f"{self.operations.get_player_name(player)}: {net_sign}${net_val}\n"

            
            if not self.operations.is_player_eliminated(player):
                self.check_and_eliminate_player(
                    player,
                    balance_before=finances.get('balance_before'),
                    net_change=finances.get('net'),
                )
                
        
        new_laws_info = ""
        new_laws = result.get('new_government_laws', [])
        if new_laws:
            law_labels = {
                'reduce_property_tax_5': 'Property tax reduction (15%) for favored player(s)',
                'reduce_property_tax_15': 'Property tax reduction (15%) for favored player(s)',
                'increase_property_tax_5': 'Property tax increase (15%) for unfavored player(s)',
                'increase_property_tax_15': 'Property tax increase (15%) for unfavored player(s)',
                'audit_target': 'Government audit ordered for unfavored player(s)',
                'tax_break': 'Tax break granted to favored player(s)',
            }
            lines = []
            for eff in new_laws:
                t = eff.get('type')
                label = law_labels.get(t, t or 'New law')
                lines.append(f"• {label}")
            new_laws_info = "\n---New Government Laws---\n" + "\n".join(lines) + "\n"

        strike_info = ""
        if result['strike_messages']:
            from worker import ENGINEER
            strike_lines = []
            for sm in result['strike_messages']:
                strike_lines.append(f"{sm['player_name']}: {sm['quantity']} {sm['worker_name']}s on strike for {sm['turns_remaining']} more turn(s)! (Demanding {sm['demanded_multiplier']:.2f}x salary)")
            strike_info = "\n---Strikes---\n" + "\n".join(strike_lines) + "\n"
        
        
        info = "\n".join([f"{self.operations.get_player_name(p)}: Money: ${self.operations.get_player_money(p)}" for p in result['active_players']])
        
        summary = profit_info + maintenance_info + resource_costs_info + line_maintenance_info + tax_info + net_info + new_laws_info + strike_info + "\n" + info
        self.add_message(f"Year {new_year}: {info}")
        # Defer End of Year Summary dialog so status bar, button, and refresh complete first (feels faster)
        if not ai_auto_advancing:
            QTimer.singleShot(50, lambda s=summary: QMessageBox.information(self, "End of Year Summary", s))

        # Update status bar immediately so year/money show right away
        if hasattr(self, 'update_status_bar'):
            self.update_status_bar()
        self.prune_and_refresh_messages()
        # Defer heavy refresh (DisplayMapInfo, DisplayGovernmentInfo, etc.) so End Year
        # button re-enables immediately; full refresh runs in next event loop tick.
        QTimer.singleShot(0, self._deferred_refresh_after_advance_year)
        # Defer auto-save so it doesn't block; runs after UI updates
        QTimer.singleShot(100, lambda ny=new_year: self._deferred_auto_save(ny))

        QTimer.singleShot(50, self._start_background_ai_in_year_turns)
        QTimer.singleShot(100, self._maybe_auto_advance_year_if_ai)

    def _deferred_refresh_after_advance_year(self):
        """Run refresh_all_displays in next event loop tick so End Year button re-enables first."""
        self._sync_missing_competitor_tabs_from_operations()
        self.refresh_all_displays()
    
    def _deferred_auto_save(self, current_year: int):
        """Wrapper for auto-save when deferred via QTimer (captures current_year in closure)."""
        try:
            self._auto_save_game(current_year)
        except Exception as e:
            logger.warning("Auto-save failed: %s", e)

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
            
            logger.warning("Auto-save failed: %s", e)
    
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
        
        if getattr(self, 'is_network_mode', False):
            self._cleanup_network()
            self._exit_to_main_menu(skip_confirm=True)
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
        if hasattr(self, 'game_open_news_log_action'):
            self.game_open_news_log_action.setEnabled(False)
        if hasattr(self, 'game_open_ai_watch_action'):
            self.game_open_ai_watch_action.setEnabled(False)
        
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
                from inflation_utils import set_inflation_rate_multiplier
                set_inflation_rate_multiplier(getattr(game, 'inflation_rate', 1.0))
                self.ai_watch_enabled = False  # Loaded games: AI Watch not enabled (wasn't set before start)
                self.current_player_index = current_player_index
                self.operations.set_current_player_index(current_player_index)
                
                if self.status_bar is None:
                    self._attach_game_status_bar(self.centralWidget().layout())
                
                try:
                    self.tabs.removeTab(0)
                except Exception:
                    pass
                
                self._setup_game_ui()
                self.save_game_action.setEnabled(True)
                self.advance_year_action.setEnabled(True)
                self.refresh_displays_action.setEnabled(True)
                if hasattr(self, 'game_settings_action'):
                    self.game_settings_action.setEnabled(True)
                
                QMessageBox.information(dlg, "Game Loaded", f"Game '{save_combo.currentText()}' loaded successfully.")
                dlg.close()
                QTimer.singleShot(100, self._start_background_ai_in_year_turns)
                QTimer.singleShot(150, self._maybe_auto_advance_year_if_ai)
            except Exception as e:
                QMessageBox.warning(dlg, "Load Error", f"Failed to load game: {str(e)}")
        
        load_btn.clicked.connect(do_load)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()
    
    def _setup_game_ui(self):
        self._game_messages = []
        self._message_widgets = []
        # Preserve status_messages_list if it exists (e.g. from load game path)
        if hasattr(self, 'status_messages_list') and self.status_messages_list is not None:
            self._message_widgets.append(self.status_messages_list)
        players = self.operations.get_players()
        self._all_player_indices = []
        for player in players:
            player_id = self.operations.get_player_id(player)
            player_name = player.get_name()
            player_tabs = self._create_player_tabs(player)
            self.tabs.addTab(player_tabs, player_name)
            self._all_player_indices.append(player_id)
        if getattr(self, 'ai_watch_enabled', False) and any(getattr(p, 'is_ai', False) for p in players):
            self._create_ai_watch_tab()
        self._create_news_log_tab()
        self._create_rules_tab()
        self._create_vehicles_tab()
        first_human_idx = next((i for i, p in enumerate(players) if not getattr(p, 'is_ai', False)), 0)
        self.current_player_index = self.operations.get_player_id(players[first_human_idx])
        self._our_player_id = self.current_player_index
        self.tabs.setCurrentIndex(first_human_idx)
        if players:
            first_player = players[first_human_idx]
            first_player_id = self.operations.get_player_id(first_player)
            self.assets_tab = self.player_tabs_dict[first_player_id]['assets_tab']
            self.assets_subtabs = self.player_tabs_dict[first_player_id]['assets_subtabs']
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
            self.government_tab = self.player_tabs_dict[first_player_id]['government_tab']
            self.government_layout = self.player_tabs_dict[first_player_id]['government_layout']
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
        if players:
            for player in players:
                self.DisplayLinesControls(player)
                self.DisplayWorkersControls(player)
                self.DisplayGovernmentInfo(player)
                try:
                    self.DisplayLawInfo(player)
                except Exception as e:
                    player_id = self.operations.get_player_id(player)
                    if player_id in self.player_tabs_dict:
                        self.player_tabs_dict[player_id]['law_layout'].addWidget(QLabel(f"Error loading lawsuit info: {str(e)}"))
        if players:
            for player in players:
                self.refresh_construction_tab()
        self._apply_view_tab_visibility()
        self._remove_game_tab_close_button()
        self._remove_assigned_player_tab_close_button()
        try:
            self.tabs.currentChanged.disconnect()
        except Exception:
            pass
        def _on_tab_changed(idx):
            if idx < len(self._all_player_indices):
                self.current_player_index = self._all_player_indices[idx]
                ref_id = self.current_player_index
                if ref_id in self.player_tabs_dict:
                    d = self.player_tabs_dict[ref_id]
                    self.assets_tab = d.get('assets_tab')
                    self.assets_subtabs = d.get('assets_subtabs')
                    self.lines_tab = d.get('lines_tab')
                    self.lines_layout = d.get('lines_layout')
                    self.rolling_stock_tab = d.get('rolling_stock_tab')
                    self.rolling_stock_tabs = d.get('rolling_stock_tabs')
                    self.rolling_stock_trainsets_tab = d.get('rolling_stock_trainsets_tab')
                    self.rolling_stock_trainsets_layout = d.get('rolling_stock_trainsets_layout')
                    self.rolling_stock_locomotives_tab = d.get('rolling_stock_locomotives_tab')
                    self.rolling_stock_locomotives_layout = d.get('rolling_stock_locomotives_layout')
                    self.rolling_stock_railcars_tab = d.get('rolling_stock_railcars_tab')
                    self.rolling_stock_railcars_layout = d.get('rolling_stock_railcars_layout')
                    self.workers_tab = d.get('workers_tab')
                    self.workers_layout = d.get('workers_layout')
                    self.law_tab = d.get('law_tab')
                    self.law_layout = d.get('law_layout')
                    self.government_tab = d.get('government_tab')
                    self.government_layout = d.get('government_layout')
                    for k in ('marketplace_tab', 'marketplace_main_layout', 'marketplace_tabs', 'marketplace_lines_tab',
                              'marketplace_lines_layout', 'marketplace_trainsets_tab', 'marketplace_trainsets_layout',
                              'marketplace_locomotives_tab', 'marketplace_locomotives_layout', 'marketplace_railcars_tab',
                              'marketplace_railcars_layout', 'marketplace_ferries_tab', 'marketplace_ferries_layout',
                              'marketplace_horses_trolleys_tab', 'marketplace_horses_trolleys_layout',
                              'marketplace_resources_tab', 'marketplace_resources_layout', 'marketplace_workers_tab',
                              'marketplace_workers_layout'):
                        if k in d:
                            setattr(self, k, d[k])
                    self.map_tab = d.get('map_tab')
                    self.map_layout = d.get('map_layout')
                    self.construction_tab = d.get('construction_tab')
                    self.construction_layout = d.get('construction_layout')
                    self.construction_subtabs = d.get('construction_subtabs')
                    self.in_progress_layout = d.get('in_progress_layout')
                    self.complete_jobs_layout = d.get('complete_jobs_layout')
                    if d.get('marketplace_layout'):
                        self.marketplace_layout = d['marketplace_layout']
                self._apply_view_only_state()
        self.tabs.currentChanged.connect(_on_tab_changed)
        self._apply_view_only_state()
        self.update_status_bar()
        self._update_game_session_tab_menu_actions()

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
                self.add_message("Trainset created successfully.")
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
    
    def buy_trainset_from_marketplace(self, sale_item):
        self._ensure_correct_player()
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        current_year = self.operations.get_year()
        if self.is_network_mode and self.network_client:
            try:
                marketplace = self.operations.game.get_marketplace()
                sale_items = marketplace.get_available_items(current_year)
                item_index = None
                for i, si in enumerate(sale_items):
                    if si == sale_item or (si.get_item() == sale_item.get_item() and si.get_price() == sale_item.get_price()):
                        item_index = i
                        break
                if item_index is None:
                    QMessageBox.warning(self, "Error", "Could not find item in marketplace")
                    return
                self.network_client.buy_trainset(str(item_index))
                QMessageBox.information(self, "Purchase Requested", "Purchase request sent to server. Waiting for confirmation...")
            except Exception as e:
                QMessageBox.warning(self, "Network Error", f"Failed to send purchase request: {str(e)}")
            return
        try:
            result = self.operations.buy_vehicle_complete(sale_item, current_player, current_year)
            self.DisplayMarketplaceInfo()
            self.update_status_bar()
            self.DisplayRollingStockInfo(current_player)
            QApplication.processEvents()
            self.check_and_eliminate_player(current_player)
            self.add_message(f"Purchased {result['item_name']} for ${result['price']}.")
            
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
        except Exception as e:
            QMessageBox.warning(self, "Purchase Failed", str(e))
        
    
    def buy_vehicle_from_marketplace(self, sale_item):
        self._ensure_correct_player()
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return
        
        current_year = self.operations.get_year()
        if self.is_network_mode and self.network_client:
            try:
                marketplace = self.operations.game.get_marketplace()
                sale_items = marketplace.get_available_items(current_year)
                item_index = None
                for i, si in enumerate(sale_items):
                    if si == sale_item or (si.get_item() == sale_item.get_item() and si.get_price() == sale_item.get_price()):
                        item_index = i
                        break
                if item_index is None:
                    QMessageBox.warning(self, "Error", "Could not find item in marketplace")
                    return
                self.network_client.buy_trainset(str(item_index))
                QMessageBox.information(self, "Purchase Requested", "Purchase request sent to server. Waiting for confirmation...")
            except Exception as e:
                QMessageBox.warning(self, "Network Error", f"Failed to send purchase request: {str(e)}")
            return
        try:
            result = self.operations.buy_vehicle_complete(sale_item, current_player, current_year)
            self.DisplayMarketplaceInfo()
            self.update_status_bar()
            from vehicle import Locomotive, Ferry, Bus, Railcar
            item = result['item']
            if isinstance(item, Railcar):
                self.DisplayRailcarsInfo(current_player)
            else:
                self.DisplayRollingStockInfo(current_player)
            QApplication.processEvents()
            self.check_and_eliminate_player(current_player)
            self.add_message(f"Purchased {result['item_name']} for ${result['price']}.")
            
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
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            if "Out of Stock" in str(e) or "no longer available" in str(e):
                self.DisplayMarketplaceInfo()

    def buy_line_from_marketplace(self, sale_item):
        self._ensure_correct_player()
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player:
            QMessageBox.warning(self, "No Active Players", "No active players remaining.")
            return

        current_year = self.operations.get_year()
        if self.is_network_mode and self.network_client:
            try:
                marketplace = self.operations.game.get_marketplace()
                sale_items = marketplace.get_available_items(current_year)
                item_index = None
                for i, si in enumerate(sale_items):
                    if si == sale_item or (
                        si.get_item() == sale_item.get_item()
                        and si.get_seller() == sale_item.get_seller()
                    ):
                        item_index = i
                        break
                if item_index is None:
                    QMessageBox.warning(self, "Error", "Could not find line in marketplace")
                    return
                self.network_client.buy_trainset(str(item_index))
                QMessageBox.information(self, "Purchase Requested", "Purchase request sent to server. Waiting for confirmation...")
            except Exception as e:
                QMessageBox.warning(self, "Network Error", f"Failed to send purchase request: {str(e)}")
            return
        try:
            line = sale_item.get_item()
            line_name = getattr(line, 'name', 'Line')
            price = self.operations.get_effective_marketplace_price_for_player(
                current_player, sale_item.get_price(current_year=current_year),
            )
            purchased = self.operations.buy_line_by_purchase(sale_item, current_player)
            if not purchased:
                QMessageBox.warning(self, "Purchase Failed", "Insufficient funds or line unavailable.")
                return
            self.DisplayMarketplaceInfo()
            self.update_status_bar()
            self.DisplayLinesControls(current_player)
            self._schedule_ai_map_refresh()
            QApplication.processEvents()
            self.check_and_eliminate_player(current_player)
            self.add_message(f"Purchased line '{purchased.name}' for ${price}.")
            QMessageBox.information(
                self,
                "Line Purchased",
                f"Line '{purchased.name}' purchased for ${price}.\n"
                f"Remaining Money: ${self.operations.get_player_money(current_player)}",
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
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

        
        line_names = self.operations.get_player_rail_line_names(current_player) if hasattr(self.operations, 'get_player_rail_line_names') else self.operations.get_player_line_names(current_player)
        if not line_names:
            QMessageBox.information(self, "Assign Trainset", "Player has no rail lines to assign to. Trainsets can only be assigned to train (rail) lines, not bus or ferry lines.")
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
                self.add_message(f"Trainset assigned to {selected_line}.")
                QMessageBox.information(self, "Assigned", f"Trainset assigned to {selected_line}")
            except Exception as e:
                QMessageBox.warning(self, "Assignment Failed", str(e))
            dlg.close()

        ok.clicked.connect(do_assign)
        cancel.clicked.connect(dlg.close)
        dlg.exec()

    def on_edit_line_clicked(self, line, player):
        """Open the full Edit Line dialog (name, schedule, extend) from Assets tab."""
        if not line or not player:
            QMessageBox.warning(self, "Edit Line", "Invalid line or player.")
            return
        line_name = self.operations.get_line_name(line)
        self.show_line_dialog(line, line_name, player, 0, 0)
        self.DisplayLinesControls(player)
    
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
                            grid_x, grid_y = round(float(x), 6), round(float(y), 6)
                            cost = self.operations.calculate_station_cost(
                                1, grid_x, grid_y,
                                player=current_player, station_name=station_name,
                            )
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
                                self.update_status_bar()
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
                                        logger.warning("Could not clear JavaScript state: %s", e)
                                
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
        from inflation_utils import format_fare_display
        if not is_owner:
            layout.addWidget(QLabel(f"Fare: {format_fare_display(stats['fare'])}"))
        layout.addWidget(QLabel(f"Profit (this year): ${stats['profit']}"))
        layout.addWidget(QLabel(f"Maintenance: ${stats['maintenance']}"))
        
        if is_owner:
            fare_row = QHBoxLayout()
            self._add_line_fare_controls(line, fare_row)
            layout.addLayout(fare_row)
            
            name_layout = QHBoxLayout()
            name_layout.addWidget(QLabel("Line Name:"))
            name_edit = QLineEdit(stats['name'])
            name_layout.addWidget(name_edit)
            layout.addLayout(name_layout)
            
            # Schedule controls and Add Track (for lines with schedule)
            if hasattr(line, 'schedule') and hasattr(line, 'set_schedule_frequency'):
                schedule_group = QGroupBox("Schedule (trains/hour)")
                schedule_layout = QVBoxLayout()
                schedule_controls = QHBoxLayout()
                import math
                def schedule_spin_max(period: str) -> int:
                    if hasattr(line, 'get_max_frequency_per_hour'):
                        return max(0, int(math.ceil(line.get_max_frequency_per_hour(period))))
                    return 60
                peak_label = QLabel("Peak:")
                peak_spin = QSpinBox()
                peak_spin.setMinimum(0)
                peak_spin.setMaximum(schedule_spin_max('peak'))
                peak_spin.setValue(min(int(getattr(line, 'schedule', {}).get('peak', 4)), peak_spin.maximum()))
                schedule_controls.addWidget(peak_label)
                schedule_controls.addWidget(peak_spin)
                off_peak_label = QLabel("Off Peak:")
                off_peak_spin = QSpinBox()
                off_peak_spin.setMinimum(0)
                off_peak_spin.setMaximum(schedule_spin_max('off_peak'))
                off_peak_spin.setValue(min(int(getattr(line, 'schedule', {}).get('off_peak', 2)), off_peak_spin.maximum()))
                schedule_controls.addWidget(off_peak_label)
                schedule_controls.addWidget(off_peak_spin)
                night_label = QLabel("Night:")
                night_spin = QSpinBox()
                night_spin.setMinimum(0)
                night_spin.setMaximum(schedule_spin_max('night'))
                night_spin.setValue(min(int(getattr(line, 'schedule', {}).get('night', 1)), night_spin.maximum()))
                schedule_controls.addWidget(night_label)
                schedule_controls.addWidget(night_spin)
                max_freq_label = QLabel(
                    f"Max: {int(line.get_max_frequency_per_hour('peak'))}/hr"
                    if hasattr(line, 'get_max_frequency_per_hour') else ""
                )
                schedule_controls.addWidget(max_freq_label)
                schedule_layout.addLayout(schedule_controls)

                current_year = self.operations.get_year()
                schedule_cost_label = QLabel("")
                schedule_cost_label.setToolTip(
                    "Annual schedule cost per trainset, line capacity, and previous-year ridership."
                )
                schedule_layout.addWidget(schedule_cost_label)

                def refresh_schedule_cost_label():
                    year = self.operations.get_year()
                    schedule = {
                        'peak': peak_spin.value(),
                        'off_peak': off_peak_spin.value(),
                        'night': night_spin.value(),
                    }
                    annual = self.operations.get_schedule_operating_cost_per_train_year(
                        year, schedule=schedule,
                    )
                    capacity = self.operations.get_line_annual_capacity(line, schedule=schedule)
                    prev_riders = self.operations.get_line_previous_year_ridership(line)
                    total_riders = self.operations.get_line_ridership_for_year(line, year)
                    schedule_cost_label.setText(
                        f"Operating cost: ${annual:.2f} per train-year ({year})\n"
                        f"Capacity: {capacity:,} passengers/year\n"
                        f"Previous year riders: {prev_riders:,}\n"
                        f"Total riders/year: {total_riders:,}"
                    )

                refresh_schedule_cost_label()

                if hasattr(line, 'upgrade_tracks') and hasattr(line, 'num_tracks'):
                    upgrade_tracks_btn = QPushButton("Add Track")
                    upgrade_tracks_btn.setToolTip(f"Current tracks: {getattr(line, 'num_tracks', 1)}")
                    def upgrade_tracks():
                        line.upgrade_tracks(1)
                        if hasattr(self.operations, 'enforce_line_schedule_limits'):
                            self.operations.enforce_line_schedule_limits(line)
                        for spin, period in (
                            (peak_spin, 'peak'), (off_peak_spin, 'off_peak'), (night_spin, 'night')
                        ):
                            spin.setMaximum(schedule_spin_max(period))
                            actual = int(line.schedule.get(period, 0))
                            if spin.value() != actual:
                                spin.blockSignals(True)
                                spin.setValue(actual)
                                spin.blockSignals(False)
                        max_freq_label.setText(f"Max: {int(line.get_max_frequency_per_hour('peak'))}/hr")
                        upgrade_tracks_btn.setToolTip(f"Current tracks: {getattr(line, 'num_tracks', 1)}")
                        refresh_schedule_cost_label()
                    upgrade_tracks_btn.clicked.connect(upgrade_tracks)
                    schedule_controls.addWidget(upgrade_tracks_btn)

                def update_schedule():
                    line.set_schedule_frequency('peak', peak_spin.value())
                    line.set_schedule_frequency('off_peak', off_peak_spin.value())
                    line.set_schedule_frequency('night', night_spin.value())
                    for spin, period in (
                        (peak_spin, 'peak'), (off_peak_spin, 'off_peak'), (night_spin, 'night')
                    ):
                        spin.setMaximum(schedule_spin_max(period))
                        actual = int(line.schedule.get(period, 0))
                        if spin.value() != actual:
                            spin.blockSignals(True)
                            spin.setValue(actual)
                            spin.blockSignals(False)
                    if hasattr(line, 'get_max_frequency_per_hour'):
                        max_freq_label.setText(f"Max: {int(line.get_max_frequency_per_hour('peak'))}/hr")
                    refresh_schedule_cost_label()
                peak_spin.valueChanged.connect(update_schedule)
                off_peak_spin.valueChanged.connect(update_schedule)
                night_spin.valueChanged.connect(update_schedule)
                schedule_group.setLayout(schedule_layout)
                layout.addWidget(schedule_group)
            
            extend_layout = QVBoxLayout()
            extend_layout.addWidget(QLabel("Extend Line:"))
            extend_layout.addWidget(QLabel(
                "First point attaches to the closer line end; each next point connects to the prior point."
            ))
            
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
                
                extend_from_end = self.operations.extension_attach_from_closest_end(
                    line_name, new_points[0],
                )

                segment_specs = self.operations.get_extension_elevation_specs(
                    line_name, new_points, extend_from_end,
                )
                elevation_levels = self._prompt_route_elevation_levels(
                    segment_specs, parent=dlg,
                )
                if elevation_levels is None:
                    return
                
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
                    result = self.operations.extend_line_complete(
                        line_name, current_player, new_points, extend_from_end,
                        elevation_levels, cost_info=cost_info,
                    )
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
                
                self.add_message("Station updated.")
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
        line_names = (self.operations.get_player_rail_line_names(player) if hasattr(self.operations, 'get_player_rail_line_names') else self.operations.get_player_line_names(player))[:]
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
                    self.add_message(f"Trainset assigned to line '{new_line}'.")
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
                self.add_message(f"Trainset unassigned from {result['line_name']}.")
                QMessageBox.information(self, "Unassigned", f"Trainset unassigned from {result['line_name']}")
                dlg.close()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
        
        ok.clicked.connect(do_unassign)
        cancel.clicked.connect(dlg.close)
        dlg.exec()

    def on_assign_trainset_for_trainset(self, trainset, player):
        """Open assign dialog with this trainset pre-selected."""
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player or self.operations.get_player_id(current_player) != self.operations.get_player_id(player):
            return
        line_names = self.operations.get_player_rail_line_names(current_player) if hasattr(self.operations, 'get_player_rail_line_names') else self.operations.get_player_line_names(current_player)
        if not line_names:
            QMessageBox.information(self, "Assign Trainset", "Player has no rail lines to assign to. Trainsets can only be assigned to train (rail) lines, not bus or ferry lines.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Assign Trainset to Line")
        dlg.setGeometry(200, 200, 400, 200)
        l = QVBoxLayout()
        loco_name = self.operations.get_trainset_locomotive_name(trainset)
        l.addWidget(QLabel(f"Trainset: {loco_name}"))
        l.addWidget(QLabel("Select Line:"))
        line_combo = QComboBox()
        line_combo.addItems(line_names)
        l.addWidget(line_combo)
        from worker import ENGINEER
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
            selected_line = line_combo.currentText()
            eng_count = self.operations.get_player_engineer_count(current_player)
            asgn_count = self.operations.get_player_assigned_trainsets_count(current_player)
            if asgn_count >= eng_count:
                QMessageBox.warning(self, "Insufficient Engineers",
                    f"You need at least {asgn_count + 1} engineers to assign this trainset.\n"
                    f"You currently have {eng_count} engineers.")
                return
            try:
                self.operations.assign_trainset_to_line(trainset, selected_line)
                self.add_message(f"Trainset assigned to {selected_line}.")
                QMessageBox.information(self, "Assigned", f"Trainset assigned to {selected_line}")
                dlg.close()
                self.DisplayRollingStockInfo(player)
            except Exception as e:
                QMessageBox.warning(self, "Assignment Failed", str(e))

        ok.clicked.connect(do_assign)
        cancel.clicked.connect(dlg.close)
        dlg.exec()

    def on_modify_trainset_for_trainset(self, trainset, player):
        """Modify trainset: swap locomotive, add/remove railcars, or disassemble entirely."""
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player or self.operations.get_player_id(current_player) != self.operations.get_player_id(player):
            current_player = player
        if trainset not in self.operations.get_player_trainsets(current_player):
            QMessageBox.information(self, "No Trainsets", "This trainset is no longer available.")
            return

        try:
            info = self.operations.get_modify_trainset_info(current_player, trainset)
        except ValueError as e:
            QMessageBox.warning(self, "Modify Trainset", str(e))
            return

        loco = info.get('locomotive')
        loco_name = self.operations.get_trainset_locomotive_name(trainset)
        cars = info.get('cars') or []
        max_cars = info.get('max_cars', 5)

        dlg = QDialog(self)
        dlg.setWindowTitle("Modify Trainset")
        dlg.setGeometry(220, 220, 520, 420)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"<b>{loco_name}</b> — {len(cars)}/{max_cars} railcars, capacity {self.operations.get_trainset_capacity(trainset)}"))
        if cars:
            car_names = ", ".join(self.operations.get_vehicle_name(c) for c in cars)
            layout.addWidget(QLabel(f"Railcars: {car_names}"))
        else:
            layout.addWidget(QLabel("Railcars: none"))

        # Replace locomotive
        compat_locos = info.get('compatible_locomotives') or []
        loco_combo = None
        if compat_locos:
            loco_row = QHBoxLayout()
            loco_row.addWidget(QLabel("Replace locomotive:"))
            loco_combo = QComboBox()
            for l in compat_locos:
                loco_combo.addItem(getattr(l, 'name', 'Locomotive'), l)
            loco_row.addWidget(loco_combo)
            replace_loco_btn = QPushButton("Replace")
            loco_row.addWidget(replace_loco_btn)
            layout.addLayout(loco_row)

            def do_replace_loco():
                new_loco = loco_combo.currentData()
                if not new_loco:
                    return
                try:
                    result = self.operations.replace_trainset_locomotive_for_player(
                        current_player, trainset, new_loco,
                    )
                    self.add_message(
                        f"Locomotive replaced: {result['old_locomotive_name']} → {result['new_locomotive_name']}"
                    )
                    QMessageBox.information(
                        dlg, "Locomotive Replaced",
                        f"Swapped to {result['new_locomotive_name']}.\nCapacity: {result['capacity']}",
                    )
                    dlg.close()
                    self.DisplayRollingStockInfo(current_player)
                except Exception as e:
                    QMessageBox.warning(dlg, "Cannot Replace Locomotive", str(e))

            replace_loco_btn.clicked.connect(do_replace_loco)

        # Add railcar
        compat_cars = info.get('compatible_railcars') or []
        if info.get('can_add_cars') and compat_cars:
            add_row = QHBoxLayout()
            add_row.addWidget(QLabel("Add railcar:"))
            add_car_combo = QComboBox()
            for c in compat_cars:
                add_car_combo.addItem(
                    f"{self.operations.get_vehicle_name(c)} (cap {self.operations.get_vehicle_capacity(c)})", c,
                )
            add_row.addWidget(add_car_combo)
            add_car_btn = QPushButton("Add")
            add_row.addWidget(add_car_btn)
            layout.addLayout(add_row)

            def do_add_car():
                car = add_car_combo.currentData()
                if not car:
                    return
                try:
                    result = self.operations.add_railcar_to_trainset_for_player(current_player, trainset, car)
                    QMessageBox.information(
                        dlg, "Railcar Added",
                        f"Capacity: {result['capacity']} ({result['cars_count']}/{max_cars} cars)",
                    )
                    dlg.close()
                    self.DisplayRollingStockInfo(current_player)
                except Exception as e:
                    QMessageBox.warning(dlg, "Cannot Add Railcar", str(e))

            add_car_btn.clicked.connect(do_add_car)

        # Remove railcar
        if info.get('can_remove_cars') and cars:
            rem_row = QHBoxLayout()
            rem_row.addWidget(QLabel("Remove railcar:"))
            rem_car_combo = QComboBox()
            for c in cars:
                rem_car_combo.addItem(self.operations.get_vehicle_name(c), c)
            rem_row.addWidget(rem_car_combo)
            rem_car_btn = QPushButton("Remove")
            rem_row.addWidget(rem_car_btn)
            layout.addLayout(rem_row)

            def do_remove_car():
                car = rem_car_combo.currentData()
                if not car:
                    return
                try:
                    result = self.operations.remove_railcar_from_trainset_for_player(
                        current_player, trainset, railcar=car,
                    )
                    QMessageBox.information(
                        dlg, "Railcar Removed",
                        f"Removed {result['railcar_name']}.\nCapacity: {result['capacity']}",
                    )
                    dlg.close()
                    self.DisplayRollingStockInfo(current_player)
                except Exception as e:
                    QMessageBox.warning(dlg, "Cannot Remove Railcar", str(e))

            rem_car_btn.clicked.connect(do_remove_car)

        layout.addWidget(QLabel(""))
        disassemble_btn = QPushButton("Disassemble Entire Trainset")
        layout.addWidget(disassemble_btn)

        btn_layout = QHBoxLayout()
        close_btn = QPushButton("Close")
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)

        def do_disassemble():
            line_name = self.operations.get_trainset_line_assignment(trainset)
            if line_name:
                reply = QMessageBox.question(
                    dlg, "Trainset Assigned",
                    f"This trainset is assigned to line '{line_name}'. Disassembling will unassign it. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                self.operations.unassign_trainset_from_line(trainset, None)
            locomotive = self.operations.get_trainset_locomotive(trainset)
            railcars = list(self.operations.get_trainset_cars(trainset))
            self.operations.remove_player_trainset(current_player, trainset)
            if locomotive:
                self.operations.acquire_locomotive_for_player(current_player, locomotive)
            for railcar in railcars:
                self.operations.acquire_railcar_for_player(current_player, railcar)
            self.add_message("Trainset disassembled.")
            QMessageBox.information(
                dlg, "Trainset Disassembled",
                f"Locomotive: {locomotive.name if locomotive else 'None'}\n"
                f"Railcars returned: {len(railcars)}",
            )
            dlg.close()
            self.DisplayRollingStockInfo(current_player)

        disassemble_btn.clicked.connect(do_disassemble)
        close_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_unassign_trainset_for_trainset(self, trainset, player):
        """Unassign this trainset from its line."""
        current_player = self.operations.get_current_player(self.current_player_index)
        if not current_player or self.operations.get_player_id(current_player) != self.operations.get_player_id(player):
            return
        try:
            result = self.operations.unassign_trainset_complete(trainset)
            self.add_message(f"Trainset unassigned from {result['line_name']}.")
            QMessageBox.information(self, "Unassigned", f"Trainset unassigned from {result['line_name']}")
            self.DisplayRollingStockInfo(player)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
    
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
                self.add_message(f"Trainset sold for ${result['sale_price']}.")
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
                self.add_message(f"Trainset sold for ${result['sale_price']}.")
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
    
    def on_create_trainset_from_locomotive(self, locomotive, player):
        """Open create trainset dialog with this locomotive pre-selected"""
        railcars = getattr(player, 'railcars', [])
        can_have_cars = getattr(locomotive, 'can_have_railcars', True)
        if can_have_cars and not railcars:
            QMessageBox.information(self, "Create Trainset", "You have no railcars to add. Buy some from the marketplace first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Create Trainset")
        dlg.setGeometry(200, 200, 500, 400)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Locomotive: {getattr(locomotive, 'name', 'Unknown')}"))
        if can_have_cars:
            layout.addWidget(QLabel("Select Railcars (hold Ctrl to select multiple):"))
            railcar_list = QListWidget()
            railcar_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
            associated = getattr(locomotive, 'associated_railcar_name', None)
            for car in railcars:
                if associated and car.name != associated:
                    continue
                car_name = getattr(car, 'name', 'Unknown')
                cap = getattr(car, 'capacity', 0)
                railcar_list.addItem(f"{car_name} (Capacity: {cap})")
            layout.addWidget(railcar_list)
        else:
            railcar_list = None
            layout.addWidget(QLabel("(Self-contained unit, no railcars needed)"))
        btn_layout = QHBoxLayout()
        create_btn = QPushButton("Create")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)

        def do_create():
            selected_cars = []
            if can_have_cars and railcar_list:
                selected_items = railcar_list.selectedItems()
                if not selected_items:
                    QMessageBox.warning(dlg, "Error", "Please select at least one railcar.")
                    return
                selected_cars = [railcars[railcar_list.row(item)] for item in selected_items]
            try:
                result = self.operations.assemble_trainset_complete(player, locomotive, selected_cars)
                self.add_message("Trainset created successfully.")
                QMessageBox.information(dlg, "Trainset Created", "Trainset created successfully!")
                dlg.close()
                self.DisplayRollingStockInfo(player)
                self.update_status_bar()
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))

        create_btn.clicked.connect(do_create)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_assign_railcar_to_trainset(self, railcar, player):
        """Assign a railcar to an existing trainset"""
        trainsets = self.operations.get_player_trainsets(player)
        eligible = []
        for ts in trainsets:
            loco = getattr(ts, 'locomotive', None)
            if not loco or not getattr(loco, 'can_have_railcars', True):
                continue
            max_cars = getattr(loco, 'max_cars', None) or 10
            cars = self.operations.get_trainset_cars(ts)
            if len(cars) >= max_cars:
                continue
            associated = getattr(loco, 'associated_railcar_name', None)
            if associated and railcar.name != associated:
                continue
            eligible.append(ts)
        if not eligible:
            QMessageBox.information(self, "Assign to Trainset", "No trainsets can accept more railcars. Create a trainset first or your trainsets are at max capacity.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Assign Railcar to Trainset")
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Select trainset for {getattr(railcar, 'name', 'Railcar')}:"))
        combo = QComboBox()
        for ts in eligible:
            loco_name = self.operations.get_trainset_locomotive_name(ts)
            cars = self.operations.get_trainset_cars(ts)
            cap = self.operations.get_trainset_capacity(ts)
            combo.addItem(f"{loco_name} ({len(cars)} cars, cap: {cap})", ts)
        layout.addWidget(combo)
        btn_layout = QHBoxLayout()
        assign_btn = QPushButton("Assign")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(assign_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)

        def do_assign():
            ts = combo.currentData()
            if not ts:
                return
            try:
                ok = self.operations.add_railcar_to_trainset(ts, railcar)
                if ok:
                    if railcar in getattr(player, 'railcars', []):
                        player.railcars.remove(railcar)
                    QMessageBox.information(dlg, "Assigned", "Railcar added to trainset.")
                    dlg.close()
                    self.DisplayRailcarsInfo(player)
                    self.DisplayRollingStockInfo(player)
                else:
                    QMessageBox.warning(dlg, "Error", "Could not add railcar (trainset may be at max capacity).")
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))

        assign_btn.clicked.connect(do_assign)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

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
                self.DisplayRailcarsInfo(player)
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
            
            
            try:
                result = self.operations.add_railcar_to_trainset_for_player(
                    current_player, selected_trainset, selected_railcar,
                )
                QMessageBox.information(dlg, "Railcar Added",
                    f"Railcar added to trainset!\nNew capacity: {result['capacity']}\n"
                    f"Railcars: {result['cars_count']}/{max_cars}")
                dlg.close()
                self.DisplayRailcarsInfo(current_player)
                self.DisplayRollingStockInfo(current_player)
            except Exception as e:
                QMessageBox.warning(dlg, "Error", str(e))
        
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
                        if not _trainset_is_dmu_or_emu(item):
                            continue
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
                        elif not can_have_cars:
                            capacity = getattr(item, 'capacity', 0)
                            ts_key = (item_name, 0, capacity, seller)
                            if ts_key not in trainset_groups:
                                trainset_groups[ts_key] = []
                            trainset_groups[ts_key].append(sale_item)
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
                for_other_player = player_tabs_dict.get('for_other_player', False)
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
                        item_layout = QHBoxLayout()
                        item_layout.addWidget(line_info)
                        if total_qty > 0 and not for_other_player:
                            buy_button = QPushButton("Buy")
                            first_available = next(
                                (si for si in sale_items if si.get_quantity_available() > 0),
                                sale_items[0],
                            )
                            buy_button.clicked.connect(
                                lambda checked, si=first_available: self.buy_line_from_marketplace(si)
                            )
                            item_layout.addWidget(buy_button)
                        item_layout.addStretch()
                        item_widget = QWidget()
                        item_widget.setLayout(item_layout)
                        marketplace_lines_layout.addWidget(item_widget)
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
                                original_price = int(original_base_price * get_inflation_factor(current_year, max_factor=10.0))
                                sale_text = f" <span style='color: red; font-weight: bold;'>SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
                            item_layout = QHBoxLayout()
                            trainset_item = sale_item.get_item()
                            quality_info = ""
                            from vehicle import Locomotive as LocomotiveClass
                            if isinstance(trainset_item, LocomotiveClass):
                                speed = getattr(trainset_item, 'speed', 'N/A')
                                quality_info = f" | Speed: {speed} mph"
                                item_info = QLabel(
                                    f"• Self-contained trainset ({loco_name}, cap={capacity}{quality_info}): "
                                    f"${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}"
                                )
                            else:
                                if hasattr(trainset_item, 'locomotive') and trainset_item.locomotive:
                                    loco = trainset_item.locomotive
                                    speed = getattr(loco, 'speed', 'N/A')
                                    quality_info = f" | Speed: {speed} mph"
                                item_info = QLabel(
                                    f"• Trainset ({loco_name}, {num_cars} cars, cap={capacity}{quality_info}): "
                                    f"${price} (Qty: {total_qty}) | Seller: {seller}{sale_text}"
                                )
                            item_info.setTextFormat(Qt.TextFormat.RichText)
                            item_layout.addWidget(item_info)
                            if total_qty > 0 and not for_other_player:
                                buy_button = QPushButton("Buy")
                                first_available = next((si for si in sale_items if si.get_quantity_available() > 0), sale_items[0])
                                if isinstance(trainset_item, LocomotiveClass):
                                    def make_buy_loco_handler(sale_item):
                                        return lambda checked: self.buy_vehicle_from_marketplace(sale_item)
                                    buy_button.clicked.connect(make_buy_loco_handler(first_available))
                                else:
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
                        if total_qty > 0 and not for_other_player:
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
                        if total_qty > 0 and not for_other_player:
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
                        if total_qty > 0 and not for_other_player:
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
                            if total_qty > 0 and not for_other_player:
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

                if player and not for_other_player:
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

                    if not for_other_player:
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
                        lawyer_rent_btn.clicked.connect(lambda checked=False, worker=LAWYER, qty_spin=lawyer_spin, year=current_year: self.on_rent_workers_clicked(worker, lawyer_spin.value(), year))
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

                    if not for_other_player:
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

                    if not for_other_player:
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
            logger.error("Error in DisplayMarketplaceInfo: %s", e)
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
        
        try:
            self.operations.rent_workers_for_player(current_player, worker_type, quantity, current_year)
        except ValueError as e:
            QMessageBox.warning(self, "Cannot Hire Workers", str(e))
            return
        self.operations.marketplace_deduct_worker_supply(worker_type.name, quantity)
        self.operations.deduct_player_money(current_player, total_cost, reason='Worker rental')
        # Update display immediately so user sees new money before modal dialog
        self.DisplayMarketplaceInfo()
        self.DisplayWorkersControls(current_player)
        self.update_status_bar()
        self.add_message(f"Rented {quantity} {worker_type.name}(s) for ${total_cost}.")
        
        # Auto-assign construction workers if they were rented
        from worker import CONSTRUCTION_WORKER
        if worker_type == CONSTRUCTION_WORKER:
            self.operations.auto_assign_unassigned_workers(current_player)
            self.refresh_construction_tab()
        
        QMessageBox.information(self, "Workers Rented", 
                               f"Successfully rented {quantity} {worker_type.name}(s) for ${total_cost} per year.")
    
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

    
    def _build_map_overview_text(self, game_map) -> str:
        """Build sidebar map overview text without regenerating map HTML."""
        all_lines = self.operations.get_map_lines()
        map_info_text = "Map Overview\n\n"
        preset_name = getattr(game_map, 'map_preset_name', None)
        if preset_name:
            from map_preset_loader import get_map_display_name
            display_name = get_map_display_name(preset_name)
            map_info_text += f"Map Type: {display_name or preset_name}\n"
        else:
            map_info_text += "Map Type: Standard\n"
        if hasattr(game_map, 'width') and hasattr(game_map, 'height'):
            map_info_text += f"Size: {game_map.width}x{game_map.height}\n"
        if hasattr(game_map, 'start_year') and hasattr(game_map, 'end_year'):
            start_year = game_map.start_year
            end_year = game_map.end_year
            if start_year is not None and end_year is not None:
                map_info_text += f"Timeline: {start_year}-{end_year}\n"
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
                line_type_str = "Train"
                try:
                    from line import Ferry, Bus
                    if isinstance(line, Ferry):
                        line_type_str = "Ferry"
                    elif isinstance(line, Bus):
                        line_type_str = "Bus"
                except Exception:
                    pass
                map_info_text += f"\n{line_name} ({owner_name})\n"
                map_info_text += f"  Type: {line_type_str}\n"
                map_info_text += f"  Stations: {num_stations}\n"
                map_info_text += f"  Trainsets: {num_trainsets}\n"
        return map_info_text

    def _update_map_sidebars(self, map_info_text: str) -> None:
        """Update map sidebar text and line combos without reloading map HTML."""
        if not hasattr(self, 'player_tabs_dict') or not self.player_tabs_dict:
            return
        all_lines = self.operations.get_map_lines()
        first_value = next(iter(self.player_tabs_dict.values()))
        if not isinstance(first_value, dict):
            return
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            map_info_text_widget = player_tabs_dict.get('map_info_text')
            if map_info_text_widget:
                map_info_text_widget.setText(map_info_text)
            station_line_combo = player_tabs_dict.get('station_line_combo')
            current_player = None
            current_player_id = None
            players = self.operations.get_players()
            for p in players:
                if self.operations.get_player_id(p) == player_id:
                    current_player = p
                    current_player_id = self.operations.get_player_id(current_player)
                    break
            if station_line_combo:
                station_line_combo.clear()
                station_line_combo.addItem("Select line...")
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
            sell_segment_combo = player_tabs_dict.get('sell_segment_combo')
            if sell_segment_combo:
                sell_segment_combo.clear()
                sell_segment_combo.addItem("Select line...")
                if current_player:
                    sell_line_names_set = set()
                    if all_lines:
                        for line_name, line in all_lines.items():
                            owner = self.operations.get_line_owner(line)
                            if owner and self.operations.get_player_id(owner) == current_player_id:
                                if line_name not in sell_line_names_set:
                                    sell_segment_combo.addItem(line_name, line)
                                    sell_line_names_set.add(line_name)
            sell_line_combo = player_tabs_dict.get('sell_line_combo')
            if sell_line_combo:
                sell_line_combo.clear()
                sell_line_combo.addItem("Select line...")
                if current_player and all_lines:
                    sell_entire_names_set = set()
                    for line_name, line in all_lines.items():
                        owner = self.operations.get_line_owner(line)
                        if owner and self.operations.get_player_id(owner) == current_player_id:
                            if line_name not in sell_entire_names_set:
                                sell_line_combo.addItem(line_name, line)
                                sell_entire_names_set.add(line_name)

    def _build_map_lines_overlay_data(self, game_map) -> list:
        """Build line overlay data for lightweight map refresh after line creation."""
        import math
        from map_preset_loader import get_map_bounds, get_default_center_lat, get_default_center_lon

        preset_name = getattr(game_map, 'map_preset_name', None)
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        center_lat = (
            game_map.map_center_lat
            if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None
            else get_default_center_lat(preset_name)
        )
        center_lon = (
            game_map.map_center_lon
            if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None
            else get_default_center_lon(preset_name)
        )
        if preset_bounds and all(k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
            min_lat, max_lat = preset_bounds['min_lat'], preset_bounds['max_lat']
            min_lon, max_lon = preset_bounds['min_lon'], preset_bounds['max_lon']
        else:
            min_lat, max_lat = center_lat - 0.5, center_lat + 0.5
            min_lon, max_lon = center_lon - 0.5, center_lon + 0.5

        lines_data = []
        map_width_val = game_map.width if getattr(game_map, 'width', None) else 50
        map_height_val = game_map.height if getattr(game_map, 'height', None) else 50
        grid_size = max(map_width_val, map_height_val)
        use_preset_bounds = preset_bounds and all(
            k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')
        )
        if use_preset_bounds:
            preset_min_lat = preset_bounds['min_lat']
            preset_max_lat = preset_bounds['max_lat']
            preset_min_lon = preset_bounds['min_lon']
            preset_max_lon = preset_bounds['max_lon']
            preset_lat_scale = (preset_max_lat - preset_min_lat) / map_height_val if map_height_val > 0 else 0
            preset_lon_scale = (preset_max_lon - preset_min_lon) / map_width_val if map_width_val > 0 else 0
        else:
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat)) or 111.0

        if hasattr(game_map, 'lines') and game_map.lines and grid_size > 0:
            for line_name, line in game_map.lines.items():
                if not hasattr(line, 'get_route'):
                    continue
                route = line.get_route()
                if not route:
                    continue
                line_points = []
                for point in route:
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        x, y = float(point[0]), float(point[1])
                    elif hasattr(point, 'x') and hasattr(point, 'y'):
                        x, y = float(point.x), float(point.y)
                    else:
                        continue
                    if use_preset_bounds:
                        lat = preset_min_lat + y * preset_lat_scale
                        lon = preset_min_lon + x * preset_lon_scale
                    else:
                        lat_km = y - grid_size // 2
                        lon_km = x - grid_size // 2
                        lat = center_lat + (lat_km / 111.0)
                        lon = center_lon + (lon_km / lon_deg_per_km)
                    if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                        line_points.append([lat, lon])
                if len(line_points) >= 2:
                    owner = self.operations.get_line_owner(line) if self.operations else None
                    if owner is None or (self.operations and self.operations.is_player_eliminated(owner)):
                        owner_id = -1
                    else:
                        owner_id = self.operations.get_player_id(owner)
                    line_type = 'train'
                    try:
                        from line import Ferry, Bus
                        if isinstance(line, Ferry):
                            line_type = 'ferry'
                        elif isinstance(line, Bus):
                            line_type = 'bus'
                    except Exception:
                        pass
                    lines_data.append({
                        'name': line_name,
                        'points': line_points,
                        'under_construction': False,
                        'owner_id': owner_id,
                        'line_type': line_type,
                    })

        if grid_size > 0 and self.operations is not None:
            line_builds_by_name = {}
            for build in self.operations.get_build_queue():
                if build.get('type') != 'line':
                    continue
                build_data = build.get('data', {})
                line_name = build_data.get('name')
                points = build_data.get('points', [])
                if not line_name or not points:
                    continue
                line_points = []
                for point in points:
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        x, y = float(point[0]), float(point[1])
                    elif hasattr(point, 'x') and hasattr(point, 'y'):
                        x, y = float(point.x), float(point.y)
                    else:
                        continue
                    if use_preset_bounds:
                        lat = preset_min_lat + y * preset_lat_scale
                        lon = preset_min_lon + x * preset_lon_scale
                    else:
                        lat_km = y - grid_size // 2
                        lon_km = x - grid_size // 2
                        lat = center_lat + (lat_km / 111.0)
                        lon = center_lon + (lon_km / lon_deg_per_km)
                    if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                        line_points.append([lat, lon])
                if len(line_points) >= 2:
                    seg_idx = build_data.get('segment_index', 0)
                    if line_name not in line_builds_by_name:
                        lt = 'ferry' if build_data.get('is_ferry_line') else (
                            'bus' if build_data.get('is_bus_line') else 'train'
                        )
                        line_builds_by_name[line_name] = {
                            'segments': [],
                            'player': build.get('player'),
                            'line_type': lt,
                        }
                    line_builds_by_name[line_name]['segments'].append((seg_idx, line_points))
            for line_name, info in line_builds_by_name.items():
                segments = sorted(info['segments'], key=lambda x: x[0])
                merged = []
                for _idx, pts in segments:
                    for p in pts:
                        if not merged or merged[-1] != p:
                            merged.append(p)
                if len(merged) >= 2:
                    build_player = info.get('player')
                    owner_id = self.operations.get_player_id(build_player) if build_player else -1
                    lines_data.append({
                        'name': line_name,
                        'points': merged,
                        'under_construction': True,
                        'owner_id': owner_id,
                        'line_type': info.get('line_type', 'train'),
                    })

        if grid_size > 0 and self.operations is not None:
            for ext_info in self.operations.get_pending_line_extension_routes():
                ext_route = ext_info.get('points') or []
                if len(ext_route) < 2:
                    continue
                line_points = []
                for point in ext_route:
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        x, y = float(point[0]), float(point[1])
                    elif hasattr(point, 'x') and hasattr(point, 'y'):
                        x, y = float(point.x), float(point.y)
                    else:
                        continue
                    if use_preset_bounds:
                        lat = preset_min_lat + y * preset_lat_scale
                        lon = preset_min_lon + x * preset_lon_scale
                    else:
                        lat_km = y - grid_size // 2
                        lon_km = x - grid_size // 2
                        lat = center_lat + (lat_km / 111.0)
                        lon = center_lon + (lon_km / lon_deg_per_km)
                    if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                        line_points.append([lat, lon])
                if len(line_points) >= 2:
                    ext_player = ext_info.get('player')
                    owner_id = self.operations.get_player_id(ext_player) if ext_player else -1
                    lines_data.append({
                        'name': ext_info.get('line_name', ''),
                        'points': line_points,
                        'under_construction': True,
                        'owner_id': owner_id,
                        'line_type': ext_info.get('line_type', 'train'),
                    })
        return lines_data

    def _build_map_stations_overlay_data(self, game_map) -> list:
        """Build station overlay data for lightweight map refresh."""
        if not self.operations:
            return []
        current_player = None
        try:
            current_player = self.operations.get_current_player(self.current_player_index)
        except Exception:
            pass
        return self.operations.get_stations_for_map_display(game_map, current_player=current_player)

    def _invalidate_all_map_web_views(self) -> None:
        """Tell Leaflet to recalculate map size after the window or map pane resizes."""
        if not hasattr(self, 'player_tabs_dict') or not self.player_tabs_dict:
            return
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            if not isinstance(player_tabs_dict, dict):
                continue
            web_view = player_tabs_dict.get(f'map_web_view_{player_id}')
            if not web_view:
                continue
            page = self._safe_get_webview_page(web_view)
            if page:
                page.runJavaScript(_MAP_ENSURE_VISIBLE_JS)

    def _refresh_map_stations_overlay(self) -> None:
        """Redraw station markers on existing map webviews without reloading HTML."""
        import json
        game_map = self.operations.get_map() if self.operations else None
        if not game_map or not hasattr(self, 'player_tabs_dict'):
            return
        stations_data = self._build_map_stations_overlay_data(game_map)
        stations_json = json.dumps(stations_data)
        js = f"if (window.refreshMapStations) {{ window.refreshMapStations({stations_json}); }}"
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            web_view = player_tabs_dict.get(f'map_web_view_{player_id}')
            if not web_view:
                continue
            page = self._safe_get_webview_page(web_view)
            if page:
                page.runJavaScript(js)

    def _refresh_map_overlays(self) -> None:
        """Redraw lines and stations on existing map webviews without reloading HTML."""
        self._refresh_map_lines_overlay()
        self._refresh_map_stations_overlay()
        self._invalidate_all_map_web_views()

    def _refresh_map_lines_overlay(self) -> None:
        """Redraw line polylines on existing map webviews without reloading HTML."""
        import json
        game_map = self.operations.get_map() if self.operations else None
        if not game_map or not hasattr(self, 'player_tabs_dict'):
            return
        lines_data = self._build_map_lines_overlay_data(game_map)
        lines_json = json.dumps(lines_data)
        js = f"if (window.refreshMapLines) {{ window.refreshMapLines({lines_json}); }}"
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            web_view = player_tabs_dict.get(f'map_web_view_{player_id}')
            if not web_view:
                continue
            page = self._safe_get_webview_page(web_view)
            if page:
                page.runJavaScript(js)

    def _refresh_after_human_line_build(self) -> None:
        """Update UI after line build without regenerating full OSM map HTML (avoids WebEngine OOM)."""
        try:
            if not self.operations:
                return
            game_map = self.operations.get_map()
            if not game_map:
                return
            map_info_text = self._build_map_overview_text(game_map)
            self._update_map_sidebars(map_info_text)
            self._refresh_map_overlays()
            if hasattr(self, 'refresh_construction_tab'):
                self.refresh_construction_tab()
            if hasattr(self, 'update_status_bar'):
                self.update_status_bar()
            current_player = self.operations.get_current_player(self.current_player_index)
            if current_player and hasattr(self, 'DisplayLinesControls'):
                self.DisplayLinesControls(current_player)
        except Exception as e:
            logger.error("Error in _refresh_after_human_line_build: %s", e)

    def _is_map_tab_visible_for_player(self, player_id) -> bool:
        """True when the player's Map subtab is the active, visible tab."""
        if not getattr(self, 'player_tabs_dict', None):
            return False
        player_tabs_dict = self.player_tabs_dict.get(player_id)
        if not isinstance(player_tabs_dict, dict):
            return False
        player_tabs = player_tabs_dict.get('player_tabs')
        map_tab_index = player_tabs_dict.get('map_tab_index')
        if player_tabs is None or not isinstance(map_tab_index, int) or map_tab_index < 0:
            return False
        try:
            if not player_tabs.isTabVisible(map_tab_index):
                return False
            if player_tabs.currentIndex() != map_tab_index:
                return False
        except Exception:
            return False
        if hasattr(self, 'tabs') and self.tabs is not None:
            our_id = getattr(self, '_our_player_id', None)
            player_tab_widget = player_tabs_dict.get('player_tabs')
            if player_id == our_id and player_tab_widget is not None:
                for i in range(self.tabs.count()):
                    if self.tabs.widget(i) is player_tab_widget:
                        return self.tabs.currentIndex() == i
                return False
            other_container = getattr(self, '_other_players_container', None)
            if other_container is not None and player_tab_widget is not None:
                for i in range(self.tabs.count()):
                    if self.tabs.widget(i) is other_container:
                        if self.tabs.currentIndex() != i:
                            return False
                        break
        return True

    def _schedule_map_visible_js(self, web_view, delays=(0, 100, 400, 800)) -> None:
        """Ask Leaflet to re-measure the map container after the tab becomes visible."""
        if web_view is None:
            return
        for delay_ms in delays:
            def _run(wv=web_view):
                page = self._safe_get_webview_page(wv)
                if page and wv.parent() is not None:
                    page.runJavaScript(_MAP_ENSURE_VISIBLE_JS)
            QTimer.singleShot(delay_ms, _run)

    def _ensure_map_web_view_ready(self, player_id=None, force_invalidate: bool = True) -> None:
        """Ensure the OSM map web view exists, is visible, and Leaflet has valid dimensions."""
        if not getattr(self, 'operations', None) or not getattr(self, 'player_tabs_dict', None):
            return
        if player_id is None:
            player_id = getattr(self, 'current_player_index', None)
        if player_id is None:
            return
        player_tabs_dict = self.player_tabs_dict.get(player_id)
        if not isinstance(player_tabs_dict, dict) or player_tabs_dict.get('for_other_player'):
            return

        web_view_key = f'map_web_view_{player_id}'
        web_view = player_tabs_dict.get(web_view_key)
        page_ok = False
        parent_ok = False
        if web_view is not None:
            try:
                parent_ok = web_view.parent() is not None
            except Exception:
                parent_ok = False
            page_ok = self._safe_get_webview_page(web_view) is not None

        pending = player_tabs_dict.get('map_load_pending', False)
        loaded = player_tabs_dict.get('map_html_loaded', False)
        needs_initial_load = (
            web_view is None or not loaded or not parent_ok or not page_ok
        )
        if needs_initial_load or pending:
            if hasattr(self, 'DisplayMapInfo'):
                self.DisplayMapInfo(force_reload=False)
            web_view = player_tabs_dict.get(web_view_key)
            player_tabs_dict['map_load_pending'] = False

        map_container = player_tabs_dict.get('map_container')
        map_tab = player_tabs_dict.get('map_tab')
        if map_container is not None:
            map_container.setVisible(True)
            map_container.show()
        if map_tab is not None:
            map_tab.setVisible(True)

        if web_view is None:
            return

        web_view.setVisible(True)
        web_view.show()
        web_view.raise_()
        _install_map_web_view_resize_filter(web_view, map_container)

        if force_invalidate:
            self._schedule_map_visible_js(web_view)

    def DisplayMapInfo(self, force_reload: bool = False):
        import time

        t0 = time.perf_counter()
        refresh_kind = "skipped"
        preset_name = None
        logger.debug("Map refresh started (force_reload=%s)", force_reload)
        try:
            if not hasattr(self, 'player_tabs_dict'):
                return
            if not self.operations:
                return
            game_map = self.operations.get_map()
            if not game_map:
                return
            preset_name = getattr(game_map, 'map_preset_name', None)

            # Never reload map HTML while the player is placing line points — that wipes
            # isCreatingLine / clickedPoints and aborts in-progress creation.
            if self._any_map_edit_in_progress():
                try:
                    map_info_text = self._build_map_overview_text(game_map)
                    self._update_map_sidebars(map_info_text)
                    self._refresh_map_overlays()
                    refresh_kind = "overlay-during-line-edit"
                except Exception as e:
                    logger.debug("Lightweight DisplayMapInfo during line edit failed: %s", e)
                    refresh_kind = "overlay-during-line-edit-failed"
                return
            
            all_lines = self.operations.get_map_lines()
            map_info_text = "Map Overview\n\n"
            preset_name = getattr(game_map, 'map_preset_name', None)
            if preset_name:
                from map_preset_loader import get_map_display_name
                display_name = get_map_display_name(preset_name)
                map_info_text += f"Map Type: {display_name or preset_name}\n"
            else:
                map_info_text += f"Map Type: Standard\n"
            if hasattr(game_map, 'width') and hasattr(game_map, 'height'):
                map_info_text += f"Size: {game_map.width}x{game_map.height}\n"
            if hasattr(game_map, 'start_year') and hasattr(game_map, 'end_year'):
                start_year = game_map.start_year
                end_year = game_map.end_year
                if start_year is not None and end_year is not None:
                    map_info_text += f"Timeline: {start_year}-{end_year}\n"
                elif preset_name:
                    from map_preset_loader import get_map_timeline
                    timeline = get_map_timeline(preset_name)
                    if timeline:
                        game_map.end_year = timeline.get('end_year', game_map.end_year)
                        game_map.start_year = timeline.get('start_year', game_map.start_year)
                    map_info_text += f"Timeline: {game_map.start_year}-{game_map.end_year}\n"
                else:
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
                    line_type_str = "Train"
                    try:
                        from line import Ferry, Bus
                        if isinstance(line, Ferry):
                            line_type_str = "Ferry"
                        elif isinstance(line, Bus):
                            line_type_str = "Bus"
                    except Exception:
                        pass
                    map_info_text += f"\n{line_name} ({owner_name})\n"
                    map_info_text += f"  Type: {line_type_str}\n"
                    map_info_text += f"  Stations: {num_stations}\n"
                    map_info_text += f"  Trainsets: {num_trainsets}\n"
            
            if not self.player_tabs_dict:
                return
            
            first_key = next(iter(self.player_tabs_dict.keys()))
            first_value = self.player_tabs_dict[first_key]
            
            if isinstance(first_value, dict):
                overlay_refresh_needed = False
                html_reload_players = 0
                for player_id, player_tabs_dict in self.player_tabs_dict.items():
                    map_layout = player_tabs_dict.get('map_layout')
                    map_container = player_tabs_dict.get('map_container')
                    map_info_text_widget = player_tabs_dict.get('map_info_text')
                    
                    if map_layout is None:
                        continue
                    
                    # Update sidebar map info
                    if map_info_text_widget:
                        map_info_text_widget.setText(map_info_text)

                    # Other players' tabs are read-only — skip expensive WebEngine map HTML.
                    if player_tabs_dict.get('for_other_player'):
                        continue
                    
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

                    sell_segment_combo = player_tabs_dict.get('sell_segment_combo')
                    if sell_segment_combo:
                        sell_segment_combo.clear()
                        sell_segment_combo.addItem("Select line...")
                        if current_player:
                            sell_line_names_set = set()
                            if all_lines:
                                for line_name, line in all_lines.items():
                                    owner = self.operations.get_line_owner(line)
                                    if owner and self.operations.get_player_id(owner) == current_player_id:
                                        if line_name not in sell_line_names_set:
                                            sell_segment_combo.addItem(line_name, line)
                                            sell_line_names_set.add(line_name)

                    sell_line_combo = player_tabs_dict.get('sell_line_combo')
                    if sell_line_combo:
                        sell_line_combo.clear()
                        sell_line_combo.addItem("Select line...")
                        if current_player and all_lines:
                            sell_entire_names_set = set()
                            for line_name, line in all_lines.items():
                                owner = self.operations.get_line_owner(line)
                                if owner and self.operations.get_player_id(owner) == current_player_id:
                                    if line_name not in sell_entire_names_set:
                                        sell_line_combo.addItem(line_name, line)
                                        sell_entire_names_set.add(line_name)
                    
                    # Get map tab and ensure it's visible
                    map_tab = player_tabs_dict.get('map_tab')
                    if map_tab is None:
                        continue
                    
                    # map_layout was already retrieved and validated above at line 4263
                    # If it was None, we would have already continued, so it's safe to use here
                    
                    # Check if web_view already exists - if so, reuse when valid (like qt.py)
                    existing_web_view = player_tabs_dict.get(f'map_web_view_{player_id}')
                    existing_refresh_btn = player_tabs_dict.get('refresh_map_btn')
                    
                    if existing_web_view:
                        try:
                            if existing_web_view.parent() is None:
                                existing_web_view = None
                            else:
                                page = self._safe_get_webview_page(existing_web_view)
                                if page is None:
                                    existing_web_view = None
                        except Exception:
                            existing_web_view = None

                    map_tab_visible = self._is_map_tab_visible_for_player(player_id)

                    # Defer WebEngine/Leaflet init until the Map tab is visible. Loading while
                    # hidden (e.g. game start on Assets tab) leaves a blank map that overlay
                    # refresh cannot fix.
                    if not map_tab_visible and not force_reload:
                        if not (existing_web_view and player_tabs_dict.get('map_html_loaded')):
                            player_tabs_dict['map_load_pending'] = True
                        if existing_web_view and player_tabs_dict.get('map_html_loaded'):
                            _install_map_web_view_resize_filter(existing_web_view, map_container)
                            overlay_refresh_needed = True
                        continue

                    player_tabs_dict['map_load_pending'] = False

                    # Reuse loaded map HTML — only refresh line overlays (avoids WebEngine OOM
                    # and preserves in-progress line creation / extension point placement).
                    if (
                        existing_web_view
                        and not force_reload
                        and player_tabs_dict.get('map_html_loaded')
                    ):
                        _install_map_web_view_resize_filter(existing_web_view, map_container)
                        if map_container is not None:
                            map_container.setVisible(True)
                        existing_web_view.setVisible(True)
                        existing_web_view.show()
                        self._schedule_map_visible_js(existing_web_view)
                        overlay_refresh_needed = True
                        continue

                    html_reload_players += 1
                    if not existing_web_view:
                        # Safely clear map_layout before creating new
                        try:
                            while map_layout.count():
                                child = map_layout.takeAt(0)
                                if child and child.widget():
                                    widget = child.widget()
                                    widget.setParent(None)
                                    widget.deleteLater()
                        except Exception as e:
                            logger.debug("Error clearing map_layout: %s", e)
                    
                    map_sidebar = player_tabs_dict.get('map_sidebar')
                    if map_sidebar:
                        map_sidebar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                        map_sidebar.setEnabled(True)
                        map_sidebar.setVisible(True)
                    
                    map_layout.setContentsMargins(5, 5, 5, 5)
                    map_layout.setSpacing(5)
                    
                    # Add refresh button when creating new (like qt.py)
                    if not existing_web_view:
                        refresh_btn = QPushButton("Refresh Map")
                        refresh_btn.clicked.connect(self.DisplayMapInfo)
                        refresh_btn.setMinimumHeight(30)
                        refresh_btn.setMinimumWidth(100)
                        map_layout.addWidget(refresh_btn, 0)
                        player_tabs_dict['refresh_map_btn'] = refresh_btn
                    else:
                        refresh_btn = existing_refresh_btn
                    
                    game_map = self.operations.get_map()
                    osm_map_created = existing_web_view is not None
                    
                    should_use_osm = hasattr(game_map, 'use_osm') and game_map.use_osm
                    
                    if should_use_osm and WEBENGINE_AVAILABLE and QWebEngineView is not None:
                        try:
                            app = QApplication.instance()
                            if app is None:
                                logger.error("QApplication instance not found!")
                                raise RuntimeError("QApplication instance required for QWebEngineView")
                            
                            if existing_web_view:
                                web_view = existing_web_view
                                logger.debug("Reusing existing web_view for player %s", player_id)
                            else:
                                logger.debug("Creating new web_view")
                                logger.debug("Creating QWebEngineView...")
                                web_view = QWebEngineView(map_container or map_tab)
                            try:
                                settings = web_view.settings()
                                settings.setAttribute(
                                    QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
                                )
                                settings.setAttribute(
                                    QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
                                )
                                prof = web_view.page().profile()
                                prof.setHttpUserAgent(
                                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                                )
                                prof.setHttpAcceptLanguage("en-US,en;q=0.9")
                                if OSMUrlRequestInterceptor is not None:
                                    prof.setUrlRequestInterceptor(OSMUrlRequestInterceptor())
                            except Exception:
                                pass
                            logger.debug("QWebEngineView created: %s", web_view)
                            
                            web_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                            web_view.setMinimumSize(400, 300)
                            web_view.setMaximumSize(16777215, 16777215)
                            web_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                            if not existing_web_view:
                                logger.debug("Adding web_view to map_layout...")
                                map_layout.addWidget(web_view, 1)
                            
                            player_tabs_dict[f'map_web_view_{player_id}'] = web_view
                            _install_map_web_view_resize_filter(web_view, map_container)
                            
                            # Sync load for both new and existing (like qt.py, but with _load_map_from_file for OSM tiles)
                            logger.debug("Generating HTML content...")
                            for _ in range(3):
                                QApplication.processEvents()
                            html_content = self._generate_osm_map_html(game_map)
                            for _ in range(3):
                                QApplication.processEvents()
                            
                            def setup_after_load(success):
                                if success:
                                    try:
                                        player_tabs_dict['map_html_loaded'] = True
                                        player_tabs_dict['map_load_pending'] = False
                                        self._setup_map_event_handlers(web_view, player_id, player_tabs_dict)
                                        self._schedule_map_visible_js(web_view)
                                    except Exception as e:
                                        logger.debug("Error in setup_after_load: %s", e)
                                else:
                                    player_tabs_dict['map_html_loaded'] = False
                                    player_tabs_dict['map_load_pending'] = True
                            
                            try:
                                if web_view.receivers(web_view.loadFinished) > 0:
                                    try:
                                        web_view.loadFinished.disconnect()
                                    except Exception:
                                        pass
                                _load_map_from_file(web_view, html_content, player_id, setup_after_load)
                            except Exception as e:
                                logger.error("Error loading map: %s", e)
                            
                            osm_map_created = True
                            logger.debug("SUCCESS: Map web view created for player %s", player_id)
                        except Exception as e:
                            import traceback
                            logger.error("ERROR creating QWebEngineView: %s", e)
                            logger.debug(traceback.format_exc())
                            # Fall back to canvas rendering
                            osm_map_created = False
                            logger.debug("Falling back to canvas rendering")
                    else:
                        logger.debug("Skipping OSM map creation (should_use_osm=%s, WEBENGINE_AVAILABLE=%s)", should_use_osm, WEBENGINE_AVAILABLE)
                        # Fallback: use QTextBrowser with static tiles when WebEngine unavailable
                        if should_use_osm:
                            try:
                                fallback_html = self._generate_simple_map_html_no_webengine(game_map)
                                fallback_browser = QTextBrowser(map_container or map_tab)
                                fallback_browser.setOpenExternalLinks(True)
                                fallback_browser.setHtml(fallback_html)
                                fallback_browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                                fallback_browser.setMinimumSize(400, 300)
                                map_layout.addWidget(fallback_browser, 1)
                                osm_map_created = True
                                logger.debug("Using QTextBrowser fallback (static map, no interactivity)")
                            except Exception as e:
                                import traceback
                                logger.error("Fallback map failed: %s", e)
                                logger.debug(traceback.format_exc())
                    
                    if osm_map_created:
                        web_view = player_tabs_dict.get(f'map_web_view_{player_id}')
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

                if overlay_refresh_needed:
                    self._refresh_map_overlays()
                    refresh_kind = "overlay"
                elif html_reload_players:
                    refresh_kind = f"full-reload({html_reload_players})"
                else:
                    refresh_kind = "sidebar-only"

        except Exception as e:
            import traceback
            refresh_kind = "error"
            logger.error("ERROR in DisplayMapInfo: %s", e)
            logger.debug(traceback.format_exc())
        finally:
            logger.debug(
                "Map refresh finished (%s, force_reload=%s, preset=%s) in %.3fs",
                refresh_kind,
                force_reload,
                preset_name or "standard",
                time.perf_counter() - t0,
            )
    
    def _connect_map_sidebar_buttons(self, player_id, player_tabs_dict):
        if player_tabs_dict.get('for_other_player'):
            # Read-only map view: disable all sidebar buttons
            for key in ('create_line_btn', 'finish_line_btn', 'cancel_line_btn', 'extend_line_btn',
                        'finish_extend_btn', 'cancel_extend_btn', 'line_name_input', 'extend_line_combo',
                        'sell_segment_btn', 'sell_segment_combo', 'cancel_sell_segment_btn',
                        'sell_line_btn', 'sell_line_combo',
                        'station_name_input', 'station_line_combo'):
                w = player_tabs_dict.get(key)
                if w is not None:
                    w.setEnabled(False)
            map_sidebar = player_tabs_dict.get('map_sidebar')
            if map_sidebar:
                for child in map_sidebar.findChildren(QPushButton):
                    child.setEnabled(False)
            return
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
                    self._set_line_creation_active(current_player_tabs_dict, True)
                    logger.debug("Started line creation for player %s: %s", player_id, line_name)
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
                self._set_line_creation_active(current_player_tabs_dict, False)
                
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

                if current_player_tabs_dict.get('line_creation_finishing'):
                    return
                current_player_tabs_dict['line_creation_finishing'] = True

                def process_line_points(points_json):
                    """Run on the Qt main loop — never open modal dialogs from a WebEngine JS callback."""
                    import json

                    def _reenable_finish():
                        current_player_tabs_dict['line_creation_finishing'] = False
                        fl_btn = current_player_tabs_dict.get('finish_line_btn')
                        if fl_btn:
                            fl_btn.setEnabled(True)

                    try:
                        if not points_json:
                            QMessageBox.warning(self, "Error", "No points received from map.")
                            _reenable_finish()
                            return

                        latlon_points = json.loads(points_json) if isinstance(points_json, str) else points_json
                        if not latlon_points or len(latlon_points) < 2:
                            QMessageBox.warning(self, "Error", "A line needs at least 2 points.")
                            _reenable_finish()
                            return

                        try:
                            grid_points = self.operations.convert_latlon_points_to_grid(latlon_points)
                        except Exception as e:
                            QMessageBox.warning(self, "Error", str(e))
                            _reenable_finish()
                            return
                        if not grid_points or len(grid_points) < 2:
                            QMessageBox.warning(self, "Error", "Failed to convert coordinates.")
                            _reenable_finish()
                            return

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
                            _reenable_finish()
                            return

                        selected_type = line_type_combo.currentText()
                        is_ferry_line = (selected_type == 'Ferry Line')
                        is_bus_line = (selected_type == 'Bus Line')

                        if is_ferry_line:
                            elevation_levels = ['water'] * (len(grid_points) - 1)
                        else:
                            segment_specs = self.operations.get_line_creation_elevation_specs(grid_points)
                            elevation_levels = self._prompt_route_elevation_levels(
                                segment_specs, is_bus_line=is_bus_line,
                            )
                            if elevation_levels is None:
                                _reenable_finish()
                                return

                        try:
                            cost_info = self.operations.calculate_line_cost(
                                grid_points,
                                elevation_levels,
                                is_bus_line=is_bus_line,
                                is_ferry_line=is_ferry_line,
                            )
                        except Exception as e:
                            QMessageBox.warning(self, "Error", str(e))
                            _reenable_finish()
                            return

                        reply = QMessageBox.question(
                            self, "Confirm Line Creation",
                            f"Creating this line will cost ${cost_info['cost']} "
                            f"(distance: {cost_info['length']:.2f} units). Proceed?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        )
                        if reply != QMessageBox.StandardButton.Yes:
                            _reenable_finish()
                            return

                        line_creation_timeout_timer = None

                        def handle_line_creation_result(res=None, err_occurred=False, err_msg=""):
                            nonlocal line_creation_timeout_timer
                            if line_creation_timeout_timer is not None:
                                line_creation_timeout_timer.stop()
                                line_creation_timeout_timer = None

                            if err_occurred:
                                QMessageBox.warning(self, "Error", f"Failed to create line: {err_msg}")
                                _reenable_finish()
                                return
                            if not res:
                                QMessageBox.warning(self, "Error", "Failed to create line.")
                                _reenable_finish()
                                return

                            if res.get('success') is False:
                                error_message = res.get('error_message', 'Unknown error')
                                if is_ferry_line:
                                    QMessageBox.warning(self, "Cannot Create Ferry Line", error_message)
                                elif is_bus_line:
                                    QMessageBox.warning(self, "Cannot Create Bus Line", error_message)
                                else:
                                    QMessageBox.warning(self, "Cannot Create Line", error_message)
                                _reenable_finish()
                                return

                            if is_ferry_line:
                                line_type_str = "Ferry line"
                            elif is_bus_line:
                                line_type_str = "Bus line"
                            else:
                                line_type_str = "Line"

                            self.add_message(f"Line '{line_name}' construction started.")
                            seg_queued = res.get('segments_queued')
                            seg_note = (
                                f"\n{seg_queued} segment(s) added to the construction queue."
                                if seg_queued else ""
                            )
                            nimby = res.get('nimby_lawsuit')
                            if nimby:
                                if nimby.get('agency_won'):
                                    self.add_message(
                                        f"NIMBYs sued {current_player.get_name()} over the new line "
                                        f"({nimby.get('excuse', 'complaint')}) — penalty ${nimby.get('penalty', 0)}."
                                    )
                                else:
                                    self.add_message(
                                        f"NIMBYs challenged {current_player.get_name()} over the new line "
                                        f"({nimby.get('excuse', 'complaint')}) — your lawyers won."
                                    )
                            QMessageBox.information(
                                self, f"{line_type_str} Created",
                                f"Line '{line_name}' is being built. Estimated completion: "
                                f"{res['build_time']} turns.{seg_note}",
                            )

                            finish_web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                            if not finish_web_view:
                                finish_web_view = self.map_web_view
                            finish_page = self._safe_get_webview_page(finish_web_view)
                            if finish_page:
                                finish_page.runJavaScript("window.cancelLineCreation();")
                            self._set_line_creation_active(current_player_tabs_dict, False)

                            create_btn = current_player_tabs_dict.get('create_line_btn')
                            finish_btn = current_player_tabs_dict.get('finish_line_btn')
                            cancel_btn = current_player_tabs_dict.get('cancel_line_btn')
                            line_points_label = current_player_tabs_dict.get('line_points_label')
                            if create_btn:
                                create_btn.setEnabled(True)
                            if finish_btn:
                                finish_btn.setEnabled(False)
                            if cancel_btn:
                                cancel_btn.setEnabled(False)
                            if line_name_widget:
                                line_name_widget.clear()
                            if line_points_label:
                                line_points_label.setText("Points: 0")

                            QTimer.singleShot(300, self._refresh_after_human_line_build)

                        def start_line_creation_worker():
                            nonlocal line_creation_timeout_timer
                            fl_btn = current_player_tabs_dict.get('finish_line_btn')
                            if fl_btn:
                                fl_btn.setEnabled(False)
                            # Run on the GUI thread: Operations/map state is not thread-safe and
                            # background create_line_complete has caused process aborts.
                            try:
                                res = self.operations.create_line_complete(
                                    line_name, grid_points, current_player, elevation_levels,
                                    is_ferry_line=is_ferry_line, is_bus_line=is_bus_line,
                                    cost_info=cost_info,
                                )
                                handle_line_creation_result(res, False, "")
                            except Exception as e:
                                import traceback
                                logger.error("Error in line creation: %s", e)
                                traceback.print_exc()
                                handle_line_creation_result(None, True, str(e))

                        QTimer.singleShot(0, start_line_creation_worker)
                    except Exception as e:
                        import traceback
                        logger.error("Error processing line points: %s", e)
                        traceback.print_exc()
                        QMessageBox.warning(self, "Error", f"Failed to create line: {e}")
                        _reenable_finish()

                def handle_points(points_json):
                    # Defer out of the WebEngine JS callback — modal dialogs there abort Qt.
                    QTimer.singleShot(0, lambda pj=points_json: process_line_points(pj))

                page.runJavaScript("JSON.stringify(window.finishLineCreation());", handle_points)
            
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
                    import json
                    js_code = f"window.startLineExtension({json.dumps(selected_line)});"
                    page.runJavaScript(js_code)
                    self._set_line_extension_active(current_player_tabs_dict, True)
                    
                    finish_extend_btn = current_player_tabs_dict.get('finish_extend_btn')
                    cancel_extend_btn = current_player_tabs_dict.get('cancel_extend_btn')
                    extend_line_btn = current_player_tabs_dict.get('extend_line_btn')
                    
                    if extend_line_btn:
                        extend_line_btn.setEnabled(False)
                    if finish_extend_btn:
                        finish_extend_btn.setEnabled(True)
                    if cancel_extend_btn:
                        cancel_extend_btn.setEnabled(True)
                    extend_line_points_label = current_player_tabs_dict.get('extend_line_points_label')
                    if extend_line_points_label:
                        extend_line_points_label.setText(
                            "Click 1: nearest line end → point. Click 2+: prior point → next."
                        )
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

                    if current_player_tabs_dict.get('line_extension_finishing'):
                        return
                    current_player_tabs_dict['line_extension_finishing'] = True
                    feb = current_player_tabs_dict.get('finish_extend_btn')
                    if feb:
                        feb.setEnabled(False)

                    def process_extend_points(points_json):
                        import json

                        def _reenable_finish_extend():
                            current_player_tabs_dict['line_extension_finishing'] = False
                            feb2 = current_player_tabs_dict.get('finish_extend_btn')
                            if feb2:
                                feb2.setEnabled(True)

                        def _finish_extension_ui(result):
                            if not result:
                                QMessageBox.warning(self, "Error", "Failed to extend line.")
                                _reenable_finish_extend()
                                return

                            self.add_message(f"Line '{selected_line}' extension started.")
                            QMessageBox.information(
                                self, "Line Extended",
                                f"Line '{selected_line}' extension is being built. "
                                f"Estimated completion: {result['build_time']} turns.",
                            )

                            finish_web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
                            if not finish_web_view:
                                finish_web_view = self.map_web_view
                            finish_page = self._safe_get_webview_page(finish_web_view)
                            if finish_page:
                                finish_page.runJavaScript("window.cancelLineExtension();")
                            self._set_line_extension_active(current_player_tabs_dict, False)

                            elb = current_player_tabs_dict.get('extend_line_btn')
                            feb3 = current_player_tabs_dict.get('finish_extend_btn')
                            ceb = current_player_tabs_dict.get('cancel_extend_btn')
                            epl = current_player_tabs_dict.get('extend_line_points_label')
                            if elb:
                                elb.setEnabled(True)
                            if feb3:
                                feb3.setEnabled(False)
                            if ceb:
                                ceb.setEnabled(False)
                            if epl:
                                epl.setText("Points: 0")

                            QTimer.singleShot(200, self._refresh_after_human_line_build)

                        try:
                            if not points_json:
                                QMessageBox.warning(self, "Error", "No points received from map.")
                                _reenable_finish_extend()
                                return

                            latlon_points = json.loads(points_json) if isinstance(points_json, str) else points_json
                            extend_from_end = None
                            if isinstance(latlon_points, dict):
                                extend_from_end = latlon_points.get('extendFromEnd')
                                latlon_points = latlon_points.get('points') or []
                            if not latlon_points or len(latlon_points) < 1:
                                QMessageBox.warning(self, "Error", "At least one point is required to extend a line.")
                                _reenable_finish_extend()
                                return

                            try:
                                grid_points = self.operations.convert_latlon_points_to_grid(latlon_points)
                            except Exception as e:
                                QMessageBox.warning(self, "Error", str(e))
                                _reenable_finish_extend()
                                return
                            if not grid_points or len(grid_points) < 1:
                                QMessageBox.warning(self, "Error", "Failed to convert coordinates.")
                                _reenable_finish_extend()
                                return
                            if extend_from_end is None:
                                extend_from_end = self.operations.extension_attach_from_closest_end(
                                    selected_line, grid_points[0],
                                )

                            segment_specs = self.operations.get_extension_elevation_specs(
                                selected_line, grid_points, extend_from_end,
                            )
                            is_ferry_line, is_bus_line = self.operations.get_line_build_type_flags(selected_line)
                            if is_ferry_line:
                                elevation_levels = ['water'] * len(segment_specs)
                            else:
                                elevation_levels = self._prompt_route_elevation_levels(
                                    segment_specs, is_bus_line=is_bus_line,
                                )
                            if elevation_levels is None:
                                _reenable_finish_extend()
                                return

                            try:
                                app = QApplication.instance()
                                if app:
                                    app.processEvents()
                                cost_info = self.operations.calculate_extend_cost(
                                    selected_line, grid_points, extend_from_end, elevation_levels,
                                )
                                if app:
                                    app.processEvents()
                            except Exception as e:
                                QMessageBox.warning(self, "Error", str(e))
                                _reenable_finish_extend()
                                return

                            if not cost_info:
                                QMessageBox.warning(self, "Error", "Failed to calculate extension cost.")
                                _reenable_finish_extend()
                                return

                            reply = QMessageBox.question(
                                self, "Confirm Line Extension",
                                f"Extending this line will cost ${cost_info['cost']} "
                                f"(distance: {cost_info['length']:.2f} units). Proceed?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            )
                            if reply != QMessageBox.StandardButton.Yes:
                                _reenable_finish_extend()
                                return

                            num_segments = max(1, len(grid_points))
                            extension_timeout_timer = None
                            epl = current_player_tabs_dict.get('extend_line_points_label')
                            if epl:
                                epl.setText("Processing extension…")

                            def _clear_extension_timeout():
                                nonlocal extension_timeout_timer
                                if extension_timeout_timer is not None:
                                    extension_timeout_timer.stop()
                                    extension_timeout_timer = None

                            def start_extend_worker():
                                app = QApplication.instance()
                                if app:
                                    app.processEvents()
                                try:
                                    ext_result = self.operations.extend_line_complete(
                                        selected_line, current_player, grid_points,
                                        extend_from_end, elevation_levels,
                                        cost_info=cost_info,
                                    )
                                    _clear_extension_timeout()
                                    if app:
                                        app.processEvents()
                                    _finish_extension_ui(ext_result)
                                except Exception as e:
                                    _clear_extension_timeout()
                                    QMessageBox.warning(self, "Error", str(e))
                                    _reenable_finish_extend()

                            def _on_extension_timeout():
                                nonlocal extension_timeout_timer
                                extension_timeout_timer = None
                                QMessageBox.information(
                                    self, "Still Working",
                                    "Line extension is taking longer than expected "
                                    f"(max {num_segments * 5} seconds). You can continue using the app; "
                                    "you'll be notified when it finishes.",
                                )

                            extension_timeout_timer = QTimer(self)
                            extension_timeout_timer.setSingleShot(True)
                            extension_timeout_timer.timeout.connect(_on_extension_timeout)
                            extension_timeout_timer.start(
                                LINE_EXTENSION_MAX_MS_PER_SEGMENT * num_segments,
                            )
                            QTimer.singleShot(0, start_extend_worker)
                        except Exception as e:
                            import traceback
                            logger.error("Error processing extend points: %s", e)
                            traceback.print_exc()
                            QMessageBox.warning(self, "Error", f"Failed to extend line: {e}")
                            _reenable_finish_extend()

                    def handle_extend_points(points_json):
                        QTimer.singleShot(0, lambda pj=points_json: process_extend_points(pj))

                    page.runJavaScript("JSON.stringify(window.finishLineExtension());", handle_extend_points)
                
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
                    self._set_line_extension_active(current_player_tabs_dict, False)
                    current_player_tabs_dict['line_extension_finishing'] = False
                    
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

        sell_segment_btn = player_tabs_dict.get('sell_segment_btn')
        cancel_sell_segment_btn = player_tabs_dict.get('cancel_sell_segment_btn')
        if sell_segment_btn:
            receivers = sell_segment_btn.receivers(sell_segment_btn.clicked)
            if receivers > 0:
                sell_segment_btn.clicked.disconnect()

            def start_sell_segment():
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    QMessageBox.warning(self, "Error", "Player tabs not found.")
                    return
                sell_segment_combo = current_player_tabs_dict.get('sell_segment_combo')
                if not sell_segment_combo:
                    QMessageBox.warning(self, "Error", "Line selector not found.")
                    return
                selected_line = sell_segment_combo.currentText()
                if not selected_line or selected_line == "Select line...":
                    QMessageBox.warning(self, "Error", "Please select a line first.")
                    return
                line = self.operations.get_map_lines().get(selected_line)
                if line is None:
                    QMessageBox.warning(self, "Error", f"Line '{selected_line}' is not built yet.")
                    return
                web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                if not web_view:
                    web_view = self.map_web_view
                page = self._safe_get_webview_page(web_view)
                if not page:
                    QMessageBox.warning(self, "Error", "Map is not ready.")
                    return
                import json
                page.runJavaScript(f"window.startLineSegmentSell({json.dumps(selected_line)});")
                self._set_line_segment_sell_active(current_player_tabs_dict, True)
                sell_segment_btn.setEnabled(False)
                if cancel_sell_segment_btn:
                    cancel_sell_segment_btn.setEnabled(True)
                sell_segment_points_label = current_player_tabs_dict.get('sell_segment_points_label')
                if sell_segment_points_label:
                    sell_segment_points_label.setText("Click 2 points on the selected line.")

            sell_segment_btn.clicked.connect(start_sell_segment)

        if cancel_sell_segment_btn:
            receivers = cancel_sell_segment_btn.receivers(cancel_sell_segment_btn.clicked)
            if receivers > 0:
                cancel_sell_segment_btn.clicked.disconnect()

            def cancel_sell_segment():
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    return
                web_view = current_player_tabs_dict.get(f'map_web_view_{player_id}')
                if not web_view:
                    web_view = self.map_web_view
                page = self._safe_get_webview_page(web_view)
                if page:
                    page.runJavaScript("window.cancelLineSegmentSell();")
                self._set_line_segment_sell_active(current_player_tabs_dict, False)
                current_player_tabs_dict['line_segment_sell_finishing'] = False
                ssb = current_player_tabs_dict.get('sell_segment_btn')
                csb = current_player_tabs_dict.get('cancel_sell_segment_btn')
                spl = current_player_tabs_dict.get('sell_segment_points_label')
                if ssb:
                    ssb.setEnabled(True)
                if csb:
                    csb.setEnabled(False)
                if spl:
                    spl.setText("Clicks: 0/2")

            cancel_sell_segment_btn.clicked.connect(cancel_sell_segment)

        sell_line_btn = player_tabs_dict.get('sell_line_btn')
        if sell_line_btn:
            receivers = sell_line_btn.receivers(sell_line_btn.clicked)
            if receivers > 0:
                sell_line_btn.clicked.disconnect()

            def sell_entire_line():
                current_player_tabs_dict = self.player_tabs_dict.get(player_id)
                if not current_player_tabs_dict:
                    QMessageBox.warning(self, "Error", "Player tabs not found.")
                    return
                sell_line_combo = current_player_tabs_dict.get('sell_line_combo')
                if not sell_line_combo:
                    QMessageBox.warning(self, "Error", "Line selector not found.")
                    return
                selected_line = sell_line_combo.currentText()
                if not selected_line or selected_line == "Select line...":
                    QMessageBox.warning(self, "Error", "Please select a line first.")
                    return
                line = self.operations.get_map_lines().get(selected_line)
                if line is None:
                    QMessageBox.warning(self, "Error", f"Line '{selected_line}' is not built yet.")
                    return
                current_player = self.operations.get_current_player(self.current_player_index)
                if not current_player:
                    return
                if selected_line not in current_player.get_line_names():
                    QMessageBox.warning(self, "Cannot Sell Line", "You can only sell lines you own.")
                    return
                refund = self.operations.estimate_line_scrap_refund(selected_line)
                reply = QMessageBox.question(
                    self,
                    "Confirm Sell Line",
                    f"Scrap entire line '{selected_line}'?\n\n"
                    f"Estimated refund: ${refund}\n"
                    f"Trainsets will be unassigned and single-line stations removed.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                try:
                    result = self.operations.sell_line_complete(current_player, selected_line)
                    closed = result.get('closed_stations') or []
                    msg = f"Line '{selected_line}' sold for ${result.get('refund', 0)}."
                    if closed:
                        msg += f"\nClosed stations: {', '.join(closed)}"
                    QMessageBox.information(self, "Line Sold", msg)
                    self.update_status_bar()
                    self.DisplayMapInfo()
                    self.DisplayLinesControls(current_player)
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to sell line: {str(e)}")

            sell_line_btn.clicked.connect(sell_entire_line)
        
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
        
        def check_extend_line_request():
            if self._any_map_edit_in_progress():
                return
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
                if line_name not in current_player.get_line_names():
                    QMessageBox.warning(self, "Cannot Add Station", "You cannot add stations to other players' lines.")
                    if web_view and web_view.page():
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.pendingAddStation = null; window.pendingAddStationLat = null; window.pendingAddStationLon = null;")
                    return
                try:
                    grid_x, grid_y = self.operations.convert_click_to_grid(lat, lon)
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
                default_station = f"Station at ({int(grid_x)}, {int(grid_y)})"
                station_name, ok = QInputDialog.getText(
                    self, "Station Name", "Enter station name:", text=default_station,
                )
                if not ok or not station_name.strip():
                    if web_view and web_view.page():
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.pendingAddStation = null; window.pendingAddStationLat = null; window.pendingAddStationLon = null;")
                    return
                station_name = station_name.strip()
                try:
                    result = self.operations.create_station_immediate(station_name, grid_x, grid_y, current_player, [line_name])
                    self.update_status_bar()
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
            
            # Atomic read-and-clear to prevent infinite dialog loop when polling
            get_pending_js = (
                "(function(){ var ln=window.pendingAddStation, lat=window.pendingAddStationLat, lon=window.pendingAddStationLon; "
                "window.pendingAddStation=null; window.pendingAddStationLat=null; window.pendingAddStationLon=null; "
                "return (ln&&lat!=null&&lon!=null)?JSON.stringify({lineName:ln,lat:lat,lon:lon}):null; })();"
            )
            station_pending_page = self._safe_get_webview_page(web_view)
            if station_pending_page:
                station_pending_page.runJavaScript(get_pending_js, handle_result)
        
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
            
            # Check for station line click data from JavaScript (atomic read-and-clear to prevent re-processing)
            def handle_station_click(result):
                if not result or result == "null" or result == "undefined" or result is None:
                    return
                
                try:
                    import json
                    if isinstance(result, str):
                        station_data = json.loads(result)
                    else:
                        station_data = result
                    
                    if station_data and isinstance(station_data, dict) and 'lineName' in station_data:
                        line_name = station_data.get('lineName', '')
                        if 'lat' in station_data and 'lon' in station_data:
                            x, y = self.operations.convert_click_to_grid(
                                float(station_data['lat']), float(station_data['lon'])
                            )
                        elif 'x' in station_data and 'y' in station_data:
                            x = round(float(station_data['x']), 6)
                            y = round(float(station_data['y']), 6)
                        else:
                            return
                        lines = self.operations.get_lines_at_point(x, y)
                        if lines:
                            # Filter to only the matching line
                            matching_lines = [(line, name, owner) for line, name, owner in lines if name == line_name]
                            if matching_lines:
                                self.on_line_clicked(x, y, matching_lines)
                except Exception as e:
                    logger.debug("Error handling station click: %s", e)
            
            # Poll for station click data (atomic read-and-clear to prevent infinite dialog loop)
            station_click_page = self._safe_get_webview_page(web_view)
            if station_click_page:
                station_click_page.runJavaScript(
                    "(function(){ var d=window.stationClickData; window.stationClickData=null; return JSON.stringify(d||null); })();",
                    handle_station_click
                )
        
        def check_segment_sell_request():
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
            if not current_player_tabs_dict.get('line_segment_sell_active'):
                return
            if current_player_tabs_dict.get('line_segment_sell_finishing'):
                return
            web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
            page = self._safe_get_webview_page(web_view)
            if not page:
                return

            def handle_segment_sell(result):
                if not result or result == "null" or result == "undefined":
                    return
                try:
                    import json
                    data = json.loads(result) if isinstance(result, str) else result
                except (json.JSONDecodeError, TypeError):
                    return
                if not data or not isinstance(data, dict):
                    return
                line_name = data.get('lineName') or data.get('line_name')
                points = data.get('points') or []
                if not line_name or len(points) < 2:
                    return
                if line_name not in current_player.get_line_names():
                    QMessageBox.warning(self, "Cannot Sell Segment", "You can only sell segments on your own lines.")
                    clear_page = self._safe_get_webview_page(web_view)
                    if clear_page:
                        clear_page.runJavaScript("window.cancelLineSegmentSell();")
                    self._set_line_segment_sell_active(current_player_tabs_dict, False)
                    return

                current_player_tabs_dict['line_segment_sell_finishing'] = True

                def process_segment_sell():
                    try:
                        grid_points = self.operations.convert_latlon_points_to_grid(points[:2])
                    except Exception as e:
                        QMessageBox.warning(self, "Error", str(e))
                        current_player_tabs_dict['line_segment_sell_finishing'] = False
                        return
                    if len(grid_points) < 2:
                        QMessageBox.warning(self, "Error", "Failed to convert click coordinates.")
                        current_player_tabs_dict['line_segment_sell_finishing'] = False
                        return

                    reply = QMessageBox.question(
                        self,
                        "Confirm Sell Segment",
                        f"Sell/remove the segment of line '{line_name}' between the two clicked points?\n\n"
                        "Stations on the removed segment will be closed. "
                        "If the line is split, two separate lines will remain.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        current_player_tabs_dict['line_segment_sell_finishing'] = False
                        clear_page = self._safe_get_webview_page(web_view)
                        if clear_page:
                            clear_page.runJavaScript("window.cancelLineSegmentSell();")
                        self._set_line_segment_sell_active(current_player_tabs_dict, False)
                        ssb = current_player_tabs_dict.get('sell_segment_btn')
                        csb = current_player_tabs_dict.get('cancel_sell_segment_btn')
                        spl = current_player_tabs_dict.get('sell_segment_points_label')
                        if ssb:
                            ssb.setEnabled(True)
                        if csb:
                            csb.setEnabled(False)
                        if spl:
                            spl.setText("Clicks: 0/2")
                        return

                    try:
                        result = self.operations.sell_line_segment_complete(
                            current_player, line_name, grid_points[0], grid_points[1],
                        )
                    except Exception as e:
                        QMessageBox.warning(self, "Error", str(e))
                        current_player_tabs_dict['line_segment_sell_finishing'] = False
                        return

                    clear_page = self._safe_get_webview_page(web_view)
                    if clear_page:
                        clear_page.runJavaScript("window.cancelLineSegmentSell();")
                    self._set_line_segment_sell_active(current_player_tabs_dict, False)
                    current_player_tabs_dict['line_segment_sell_finishing'] = False
                    ssb = current_player_tabs_dict.get('sell_segment_btn')
                    csb = current_player_tabs_dict.get('cancel_sell_segment_btn')
                    spl = current_player_tabs_dict.get('sell_segment_points_label')
                    if ssb:
                        ssb.setEnabled(True)
                    if csb:
                        csb.setEnabled(False)
                    if spl:
                        spl.setText("Clicks: 0/2")

                    lines_msg = ", ".join(result.get('resulting_lines') or [line_name])
                    refund = int(result.get('refund') or 0)
                    split_msg = " Line was split into two." if result.get('split') else ""
                    QMessageBox.information(
                        self,
                        "Segment Sold",
                        f"Removed segment from line '{line_name}'. Remaining line(s): {lines_msg}.{split_msg}\n"
                        f"Refund: ${refund}",
                    )
                    self.update_status_bar()
                    self.DisplayLinesControls(current_player)
                    QTimer.singleShot(200, self.DisplayMapInfo)

                QTimer.singleShot(0, process_segment_sell)

            get_pending_js = (
                "(function(){ var d=window.pendingSegmentSell; window.pendingSegmentSell=null; return d || null; })();"
            )
            pending_page = self._safe_get_webview_page(web_view)
            if pending_page:
                pending_page.runJavaScript(get_pending_js, handle_segment_sell)

        def check_segment_sell_click_count():
            if self.operations is None:
                return
            if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                return
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                return
            current_player_id = self.operations.get_player_id(current_player)
            current_player_tabs_dict = self.player_tabs_dict.get(current_player_id)
            if not current_player_tabs_dict or not current_player_tabs_dict.get('line_segment_sell_active'):
                return
            web_view = current_player_tabs_dict.get(f'map_web_view_{current_player_id}')
            page = self._safe_get_webview_page(web_view)
            if not page:
                return

            def handle_count(result):
                if result and str(result).strip().isdigit():
                    count = int(str(result).strip())
                    spl = current_player_tabs_dict.get('sell_segment_points_label')
                    if spl:
                        spl.setText(f"Clicks: {count}/2")

            count_page = self._safe_get_webview_page(web_view)
            if count_page:
                count_page.runJavaScript(
                    "window.segmentSellClicks ? window.segmentSellClicks.length : 0;",
                    handle_count,
                )
        
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
            if (typeof window.pendingRenameStation === 'undefined') {
                window.pendingRenameStation = null;
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
                window.addEventListener('mapSegmentSellClicked', function(e) {
                    window.pendingSegmentSellCount = e.detail.count || 0;
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
                window.addEventListener('mapRenameStation', function(e) {
                    if (e && e.detail) {
                        window.pendingRenameStation = {
                            stationName: e.detail.stationName,
                            lat: e.detail.lat,
                            lon: e.detail.lon
                        };
                    }
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

        timer_key_segment_sell = f'segment_sell_timer_{player_id}'
        if not hasattr(self, '_segment_sell_timers'):
            self._segment_sell_timers = {}
        if timer_key_segment_sell not in self._segment_sell_timers:
            segment_sell_timer = QTimer()
            segment_sell_timer.timeout.connect(check_segment_sell_request)
            segment_sell_timer.start(300)
            self._segment_sell_timers[timer_key_segment_sell] = segment_sell_timer

        timer_key_segment_sell_count = f'segment_sell_count_timer_{player_id}'
        if not hasattr(self, '_segment_sell_count_timers'):
            self._segment_sell_count_timers = {}
        if timer_key_segment_sell_count not in self._segment_sell_count_timers:
            segment_sell_count_timer = QTimer()
            segment_sell_count_timer.timeout.connect(check_segment_sell_click_count)
            segment_sell_count_timer.start(200)
            self._segment_sell_count_timers[timer_key_segment_sell_count] = segment_sell_count_timer
        
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
                    result = self.operations.close_station(current_player, station_to_close)
                    if not result.get('success'):
                        QMessageBox.warning(self, "Cannot Close Station", result.get('message', "You cannot close this station."))
                        return
                    QMessageBox.information(self, "Station Closed", f"Station '{station_name}' has been closed.")
                    if web_view and web_view.page():
                        close_station_page = self._safe_get_webview_page(web_view)
                        if close_station_page:
                            close_station_page.runJavaScript("window.pendingCloseStation = null;")
                    self.DisplayMapInfo()
            
            close_station_check_page = self._safe_get_webview_page(web_view)
            if close_station_check_page:
                close_station_check_page.runJavaScript("window.pendingCloseStation || null;", handle_close)
        
        def check_rename_station():
            def handle_rename(result):
                if not result or result == "null" or result == "undefined":
                    return
                if self.operations is None:
                    return
                if not hasattr(self, 'current_player_index') or self.current_player_index is None:
                    return
                try:
                    import json
                    data = json.loads(result) if isinstance(result, str) else result
                except (json.JSONDecodeError, TypeError):
                    return
                if not data or not isinstance(data, dict):
                    return
                station_name = str(data.get('stationName') or data.get('station_name') or '').strip()
                lat = data.get('lat')
                lon = data.get('lon')
                if lat is None or lon is None:
                    return
                x, y = self.operations.convert_click_to_grid(float(lat), float(lon))
                stations = self.operations.get_stations_at_point(x, y)
                if not stations:
                    game_map = self.operations.get_map()
                    if hasattr(game_map, 'stations') and game_map.stations:
                        for station in game_map.stations:
                            if hasattr(station, 'name') and station.name == station_name:
                                line_names = (
                                    station.get_line_names() if hasattr(station, 'get_line_names')
                                    else getattr(station, 'line_names', [])
                                )
                                if line_names:
                                    line = self.operations.get_map_lines().get(line_names[0])
                                    if line:
                                        stations = [(station, line_names[0], line)]
                                        break
                if not stations:
                    QMessageBox.warning(self, "Station Not Found", f"Could not find station '{station_name}'.")
                    return
                if len(stations) == 1:
                    station, line_name, line = stations[0]
                    self.show_station_edit_dialog(station, line_name, line, x, y)
                else:
                    self.on_station_clicked(x, y, stations)
                rename_clear_page = self._safe_get_webview_page(web_view)
                if rename_clear_page:
                    rename_clear_page.runJavaScript("window.pendingRenameStation = null;")

            rename_check_page = self._safe_get_webview_page(web_view)
            if rename_check_page:
                rename_check_page.runJavaScript(
                    "(function(){ var d=window.pendingRenameStation; window.pendingRenameStation=null; return JSON.stringify(d||null); })();",
                    handle_rename,
                )

        timer_key_close_station = f'close_station_timer_{player_id}'
        if not hasattr(self, '_close_station_timers'):
            self._close_station_timers = {}
        if timer_key_close_station not in self._close_station_timers:
            close_station_timer = QTimer()
            close_station_timer.timeout.connect(check_close_station)
            close_station_timer.start(500)
            self._close_station_timers[timer_key_close_station] = close_station_timer

        timer_key_rename_station = f'rename_station_timer_{player_id}'
        if not hasattr(self, '_rename_station_timers'):
            self._rename_station_timers = {}
        if timer_key_rename_station not in self._rename_station_timers:
            rename_station_timer = QTimer()
            rename_station_timer.timeout.connect(check_rename_station)
            rename_station_timer.start(500)
            self._rename_station_timers[timer_key_rename_station] = rename_station_timer
        
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
        logger.debug("_generate_osm_map_html called (deadline in %.1fs)", max(0, deadline - time.time()))
        logger.debug("game_map: %s, type: %s", game_map, type(game_map))
        
        # Use map's center coordinates and presets via map_preset_loader
        from map_preset_loader import get_map_bounds, get_map_zoom, get_default_center_lat, get_default_center_lon
        from map_presets_standard import STANDARD_BOUNDS, STANDARD_ZOOM
        
        preset_name = getattr(game_map, 'map_preset_name', None)
        center_lat_check = game_map.map_center_lat if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None else None
        center_lon_check = game_map.map_center_lon if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None else None
        
        if preset_name:
            center_lat = center_lat_check if center_lat_check is not None else get_default_center_lat(preset_name)
            center_lon = center_lon_check if center_lon_check is not None else get_default_center_lon(preset_name)
        else:
            center_lat = game_map.map_center_lat if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None else STANDARD_BOUNDS['center_lat']
            center_lon = game_map.map_center_lon if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None else STANDARD_BOUNDS['center_lon']
            if center_lat is None:
                center_lat = STANDARD_BOUNDS['center_lat']
            if center_lon is None:
                center_lon = STANDARD_BOUNDS['center_lon']
        
        if preset_name:
            bounds = get_map_bounds(preset_name)
            zoom_cfg = get_map_zoom(preset_name)
            if bounds and all(k in bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
                min_lat, max_lat = bounds['min_lat'], bounds['max_lat']
                min_lon, max_lon = bounds['min_lon'], bounds['max_lon']
            else:
                min_lat = center_lat - 0.2
                max_lat = center_lat + 0.2
                min_lon = center_lon - 0.2
                max_lon = center_lon + 0.2
            if zoom_cfg:
                zoom_level = zoom_cfg.get('default_zoom', 11)
                min_zoom = zoom_cfg.get('min_zoom', 10)
                max_zoom = zoom_cfg.get('max_zoom', 18)
            else:
                zoom_level, min_zoom, max_zoom = 11, 10, 18
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
            if preset_name:
                center_lat = get_default_center_lat(preset_name)
                center_lon = get_default_center_lon(preset_name)
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
        
        # Get population data (all filtering done in operations.get_population_points_for_display)
        population_data = []
        if self.operations is not None:
            population_data = self.operations.get_population_points_for_display(game_map)
        
        # Filter points to only those within the visible map bounds
        filtered_population_data = []
        for i, point in enumerate(population_data):
            lat = point.get('lat')
            lon = point.get('lon')
            if lat is not None and lon is not None:
                if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                    filtered_population_data.append(point)
            if i > 0 and i % 1000 == 0:
                QApplication.processEvents()
        
        population_data = filtered_population_data
        logger.debug("Filtered to %d population points within map bounds", len(population_data))
        
        # elapsed_time = time.time() - start_time
        
        lines_data = []
        map_width_val = game_map.width if hasattr(game_map, 'width') and game_map.width and game_map.width > 0 else 50
        map_height_val = game_map.height if hasattr(game_map, 'height') and game_map.height and game_map.height > 0 else 50
        grid_size = max(map_width_val, map_height_val)
        
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        use_preset_bounds = preset_bounds and all(k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon'))
        if use_preset_bounds:
            preset_min_lat = preset_bounds['min_lat']
            preset_max_lat = preset_bounds['max_lat']
            preset_min_lon = preset_bounds['min_lon']
            preset_max_lon = preset_bounds['max_lon']
            preset_lat_scale = (preset_max_lat - preset_min_lat) / map_height_val if map_height_val > 0 else 0
            preset_lon_scale = (preset_max_lon - preset_min_lon) / map_width_val if map_width_val > 0 else 0
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
                            if use_preset_bounds:
                                lat = preset_min_lat + y * preset_lat_scale
                                lon = preset_min_lon + x * preset_lon_scale
                            else:
                                lat_km = (y - grid_size // 2)
                                lon_km = (x - grid_size // 2)
                                lat = center_lat + (lat_km / 111.0)
                                lon = center_lon + (lon_km / lon_deg_per_km)
                            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                                line_points.append([lat, lon])
                        if len(line_points) >= 2:
                            owner = self.operations.get_line_owner(line) if self.operations else None
                            if owner is None or (self.operations and self.operations.is_player_eliminated(owner)):
                                owner_id = -1
                            else:
                                owner_id = self.operations.get_player_id(owner)
                            line_type = 'train'
                            try:
                                from line import Ferry, Bus
                                if isinstance(line, Ferry):
                                    line_type = 'ferry'
                                elif isinstance(line, Bus):
                                    line_type = 'bus'
                            except Exception:
                                pass
                            lines_data.append({'name': line_name, 'points': line_points, 'under_construction': False, 'owner_id': owner_id, 'line_type': line_type})
                if line_idx > 0 and line_idx % 10 == 0:
                    QApplication.processEvents()
                    if time.time() > deadline:
                        logger.warning("Map HTML generation aborted during lines (timeout)")
                        return self._generate_osm_map_html_timeout_placeholder()
        
        if grid_size > 0:
            build_queue = []
            if self.operations is not None:
                build_queue = self.operations.get_build_queue()
            # Group line builds by name and merge segments so label shows at line ends, not segment ends
            line_builds_by_name = {}
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
                        if isinstance(point, (list, tuple)) and len(point) >= 2:
                            x, y = float(point[0]), float(point[1])
                        elif hasattr(point, 'x') and hasattr(point, 'y'):
                            x, y = float(point.x), float(point.y)
                        else:
                            continue
                        if use_preset_bounds:
                            lat = preset_min_lat + y * preset_lat_scale
                            lon = preset_min_lon + x * preset_lon_scale
                        else:
                            lat_km = (y - grid_size // 2)
                            lon_km = (x - grid_size // 2)
                            lat = center_lat + (lat_km / 111.0)
                            lon = center_lon + (lon_km / lon_deg_per_km)
                        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                            line_points.append([lat, lon])
                    if len(line_points) >= 2:
                        seg_idx = build_data.get('segment_index', 0)
                        if line_name not in line_builds_by_name:
                            lt = 'ferry' if build_data.get('is_ferry_line') else ('bus' if build_data.get('is_bus_line') else 'train')
                            line_builds_by_name[line_name] = {'segments': [], 'player': build.get('player'), 'line_type': lt}
                        line_builds_by_name[line_name]['segments'].append((seg_idx, line_points))
                if build_idx > 0 and build_idx % 5 == 0:
                    QApplication.processEvents()
                    if time.time() > deadline:
                        logger.warning("Map HTML generation aborted during build queue (timeout)")
                        return self._generate_osm_map_html_timeout_placeholder()
            # Merge segments per line and add one polyline per line (label at line ends only)
            for line_name, info in line_builds_by_name.items():
                segments = sorted(info['segments'], key=lambda x: x[0])
                merged = []
                for _idx, pts in segments:
                    for p in pts:
                        if not merged or merged[-1] != p:
                            merged.append(p)
                if len(merged) >= 2:
                    build_player = info.get('player')
                    owner_id = self.operations.get_player_id(build_player) if build_player and self.operations else -1
                    line_type = info.get('line_type', 'train')
                    lines_data.append({'name': line_name, 'points': merged, 'under_construction': True, 'owner_id': owner_id, 'line_type': line_type})
            if self.operations is not None:
                for ext_info in self.operations.get_pending_line_extension_routes():
                    ext_route = ext_info.get('points') or []
                    if len(ext_route) < 2:
                        continue
                    line_points = []
                    for point in ext_route:
                        if isinstance(point, (list, tuple)) and len(point) >= 2:
                            x, y = float(point[0]), float(point[1])
                        elif hasattr(point, 'x') and hasattr(point, 'y'):
                            x, y = float(point.x), float(point.y)
                        else:
                            continue
                        if use_preset_bounds:
                            lat = preset_min_lat + y * preset_lat_scale
                            lon = preset_min_lon + x * preset_lon_scale
                        else:
                            lat_km = (y - grid_size // 2)
                            lon_km = (x - grid_size // 2)
                            lat = center_lat + (lat_km / 111.0)
                            lon = center_lon + (lon_km / lon_deg_per_km)
                        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                            line_points.append([lat, lon])
                    if len(line_points) >= 2:
                        ext_player = ext_info.get('player')
                        owner_id = self.operations.get_player_id(ext_player) if ext_player else -1
                        lines_data.append({
                            'name': ext_info.get('line_name', ''),
                            'points': line_points,
                            'under_construction': True,
                            'owner_id': owner_id,
                            'line_type': ext_info.get('line_type', 'train'),
                        })
        
        if time.time() > deadline:
            logger.warning("Map HTML generation aborted before stations (timeout)")
            return self._generate_osm_map_html_timeout_placeholder()
        
        stations_data = []
        # Add stations under construction from build queue (grey)
        if self.operations is not None and grid_size > 0:
            for build in self.operations.get_build_queue():
                if build.get('type') == 'station':
                    build_data = build.get('data', {})
                    x = build_data.get('x')
                    y = build_data.get('y')
                    if x is not None and y is not None:
                        if use_preset_bounds:
                            lat = preset_min_lat + y * preset_lat_scale
                            lon = preset_min_lon + x * preset_lon_scale
                        else:
                            lat_km = (y - grid_size // 2)
                            lon_km = (x - grid_size // 2)
                            lat = center_lat + (lat_km / 111.0)
                            lon = center_lon + (lon_km / lon_deg_per_km)
                        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                            line_names_build = build_data.get('line_names', [])
                            can_close = True
                            can_rename = True
                            if self.operations and line_names_build:
                                cp = self.operations.get_current_player(self.current_player_index)
                                for ln in line_names_build:
                                    line = self.operations.get_map_lines().get(ln)
                                    if line and self.operations.get_line_owner(line) != cp:
                                        can_close = False
                                        can_rename = False
                                        break
                            num_lines = len(line_names_build or [])
                            stations_data.append({
                                'lat': lat, 'lon': lon,
                                'name': build_data.get('name', 'Station'),
                                'under_construction': True,
                                'can_close': can_close,
                                'can_rename': can_rename,
                                'line_names': line_names_build or [],
                                'is_merged': num_lines >= 2,
                                'num_lines': num_lines,
                            })
        if hasattr(game_map, 'stations') and game_map.stations and grid_size > 0:
            stations_list = list(game_map.stations)
            for station_idx, station in enumerate(stations_list):
                if time.time() > deadline:
                    logger.warning("Map HTML generation aborted during stations (timeout)")
                    return self._generate_osm_map_html_timeout_placeholder()
                if hasattr(station, 'x') and hasattr(station, 'y'):
                    x, y = station.x, station.y
                    # Convert grid coordinates to lat/lon using pre-calculated constants
                    if use_preset_bounds:
                        lat = preset_min_lat + y * preset_lat_scale
                        lon = preset_min_lon + x * preset_lon_scale
                    else:
                        lat_km = (y - grid_size // 2)
                        lon_km = (x - grid_size // 2)
                        lat = center_lat + (lat_km / 111.0)
                        lon = center_lon + (lon_km / lon_deg_per_km)
                    if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                        station_name = station.name if hasattr(station, 'name') else str(station)
                        can_close = True
                        can_rename = True
                        line_names_st = getattr(station, 'get_line_names', lambda: getattr(station, 'line_names', []))()
                        if self.operations:
                            cp = self.operations.get_current_player(self.current_player_index)
                            for ln in (line_names_st or []):
                                line = self.operations.get_map_lines().get(ln)
                                if line and self.operations.get_line_owner(line) != cp:
                                    can_close = False
                                    can_rename = False
                                    break
                        num_lines = len(line_names_st or [])
                        stations_data.append({
                            'lat': lat, 'lon': lon,
                            'name': station_name,
                            'under_construction': False,
                            'can_close': can_close,
                            'can_rename': can_rename,
                            'line_names': line_names_st or [],
                            'is_merged': num_lines >= 2,
                            'num_lines': num_lines,
                        })
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
                        if use_preset_bounds:
                            lat = preset_min_lat + y * preset_lat_scale
                            lon = preset_min_lon + x * preset_lon_scale
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
        if len(roads_data) > MAP_DISPLAY_MAX_ROADS:
            roads_data = roads_data[:MAP_DISPLAY_MAX_ROADS]
        
        # Ensure waterways display - OSM tiles automatically show waterways
        # No additional code needed as OSM tiles include water features
        

        # Performance timing
        elapsed_time = time.time() - start_time
        total_points = sum(len(road.get('points', [])) for road in roads_data) if roads_data else 0
        logger.debug("Generated HTML in %.2fs: %d roads (%d points), %d population points, %d lines",
                     elapsed_time, len(roads_data), total_points, len(population_data), len(lines_data))
        if elapsed_time > 5.0:
            logger.warning("Map HTML generation took %.2fs (target: < 5s)", elapsed_time)
        
        # OPTIMIZATION: Process events after data collection but before HTML generation
        for _ in range(5):
            QApplication.processEvents()
        
        # OPTIMIZATION: JSON serialization can be slow for large datasets
        import json
        # Apply View menu settings
        if not (hasattr(self, 'view_show_population_action') and self.view_show_population_action.isChecked()):
            population_data = []
        if not (hasattr(self, 'view_show_lines_action') and self.view_show_lines_action.isChecked()):
            lines_data = []
        try:
            population_json = json.dumps(population_data if population_data else [])
            QApplication.processEvents()
            lines_json = json.dumps(lines_data if lines_data else [])
            QApplication.processEvents()
            roads_json = json.dumps(roads_data if roads_data else [])
            QApplication.processEvents()
            stations_json = json.dumps(stations_data if stations_data else [])
            QApplication.processEvents()
        except Exception as e:
            logger.warning("JSON serialization failed: %s, using empty arrays", e)
            population_json = "[]"
            lines_json = "[]"
            roads_json = "[]"
            stations_json = "[]"

        # Use embedded Leaflet (CDN can fail when offline/blocked) + setHtml with https base URL for OSM tiles
        leaflet_css, leaflet_js = _get_embedded_leaflet()
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Railroad Baron Map</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        {leaflet_css}
        html, body {{ 
            margin: 0; 
            padding: 0; 
            width: 100%;
            height: 100%;
            overflow: hidden;
            position: relative;
        }}
        #map {{ 
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            width: 100%;
            height: 100%;
        }}
        .line-end-label {{ pointer-events: none; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        {leaflet_js}
    </script>
    <script>
        var mapInstance = null;
        function ensureMapContainerSize() {{
            var h = window.innerHeight || document.documentElement.clientHeight || 600;
            var w = window.innerWidth || document.documentElement.clientWidth || 800;
            document.documentElement.style.height = h + 'px';
            document.body.style.height = h + 'px';
            var mapEl = document.getElementById('map');
            if (mapEl) {{
                mapEl.style.height = h + 'px';
                mapEl.style.width = w + 'px';
            }}
        }}
        function initMap() {{
            if (typeof L === 'undefined') {{
                document.getElementById('map').innerHTML = '<div style="padding: 20px; color: red;">Error: Leaflet failed to load. Check internet or static files.</div>';
                return;
            }}
            if (window.mapInstance) return;
            try {{
                ensureMapContainerSize();
                mapInstance = L.map('map', {{
                    minZoom: {min_zoom},
                    maxZoom: {max_zoom},
                    scrollWheelZoom: true,
                    dragging: true,
                    touchZoom: true,
                    doubleClickZoom: true,
                    boxZoom: true,
                    keyboard: true
                }}).setView([{center_lat}, {center_lon}], {zoom_level});
            
            // OpenStreetMap tiles (standard OSM display)
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                minZoom: {min_zoom},
                maxZoom: {max_zoom},
                tileSize: 256,
                zoomOffset: 0
            }}).addTo(mapInstance);
            window.mapInstance = mapInstance;
            window.ensureMapVisible = function() {{
                ensureMapContainerSize();
                if (window.mapInstance) {{
                    window.mapInstance.invalidateSize(true);
                }}
            }};
            // Fix tiles not loading when map container had zero size at init (e.g. tab not yet shown)
            setTimeout(function() {{ if (window.ensureMapVisible) window.ensureMapVisible(); }}, 100);
            setTimeout(function() {{ if (window.ensureMapVisible) window.ensureMapVisible(); }}, 400);
            setTimeout(function() {{ if (window.ensureMapVisible) window.ensureMapVisible(); }}, 500);
            
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
        // Store population markers
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
            
            // Add expansion limit indicator (dashed circle) - only for included points
            if (item.included !== false && radius < maxRadius) {{
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
            
            var gridX = item.grid_x !== undefined ? item.grid_x : 'N/A';
            var gridY = item.grid_y !== undefined ? item.grid_y : 'N/A';
            marker.bindPopup('Population: ' + pop + '<br/>Coordinates: (' + gridX + ', ' + gridY + ')');
            marker.addTo(mapInstance);
            // Store marker for potential removal
            window.populationMarkers.push(marker);
        }});
        }}
        
        var linesData = {lines_json};
            var playerColorsHex = {json.dumps(PLAYER_COLORS_HEX)};
            var lineLayers = [];
        function renderMapLinesOntoMap(linesToRender, layerBucket) {{
        linesToRender.forEach(function(line) {{
                var isUnderConstruction = line.under_construction || false;
                var ownerId = line.owner_id !== undefined ? line.owner_id : -1;
                var lineColor;
                if (ownerId >= 0 && ownerId < playerColorsHex.length) {{
                    lineColor = playerColorsHex[ownerId];
                }} else if (isUnderConstruction) {{
                    lineColor = '#ff8800';
                }} else {{
                    lineColor = '#888888';
                }}
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
                var popupSym = (line.line_type === 'ferry') ? '⛴ ' : (line.line_type === 'bus') ? '🚌 ' : '🚂 ';
                var popupContent = '<div><b>' + popupSym + (line.name || '') + '</b><br/>' +
                    '<button class="extend-line-btn" data-line-name="' + encodedName + '">Extend Line</button><br/>' +
                    '<button class="add-station-btn" data-line-name="' + encodedName + '">Add Station</button></div>';
                lineLayer.bindPopup(popupContent);
                
                // Add click handler for station creation mode - intercept clicks before popup opens
                lineLayer.on('click', function(e) {{
                    // Store click location for Add Station (popup will use this)
                    window.lastLineClickLatLng = {{ lat: e.latlng.lat, lon: e.latlng.lng }};
                    window.lastLineClickName = line.name;
                    if (isSellingLineSegment && segmentSellLineName && line.name === segmentSellLineName) {{
                        addSegmentSellClick(e.latlng.lat, e.latlng.lng);
                        if (e.originalEvent) {{
                            e.originalEvent.preventDefault();
                            e.originalEvent.stopPropagation();
                        }}
                        return false;
                    }}
                    if (isCreatingStation && window.pendingStationLine && line.name === window.pendingStationLine) {{
                        // Store lat/lon for exact placement (Python converts to grid with 0.000001 precision)
                        var lat = e.latlng.lat;
                        var lon = e.latlng.lng;
                        var mapWidth = {map_width_val};
                        var mapHeight = {map_height_val};
                        var minLat = {min_lat};
                        var maxLat = {max_lat};
                        var minLon = {min_lon};
                        var maxLon = {max_lon};
                        var gridX = ((lon - minLon) / (maxLon - minLon)) * mapWidth;
                        var gridY = ((lat - minLat) / (maxLat - minLat)) * mapHeight;
                        window.stationClickData = {{ x: gridX, y: gridY, lat: lat, lon: lon, lineName: line.name }};
                        var event = new CustomEvent('stationLineClicked', {{
                            detail: {{ x: gridX, y: gridY, lat: lat, lon: lon, lineName: line.name }}
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
                layerBucket.push(lineLayer);
                // Add line name at both ends (small labels) with type symbol: train, ferry, or bus
                var lineLabelStyle = 'font-size:9px;font-weight:bold;color:' + lineColor + ';text-shadow:0 0 2px #fff,0 0 2px #fff;white-space:nowrap;pointer-events:none;';
                var pts = line.points;
                if (pts && pts.length >= 2 && (line.name || '')) {{
                    var first = pts[0], last = pts[pts.length - 1];
                    var sym = (line.line_type === 'ferry') ? '⛴ ' : (line.line_type === 'bus') ? '🚌 ' : '🚂 ';
                    var labelHtml = '<span style="' + lineLabelStyle + '">' + sym + (line.name || '').replace(/</g, '&lt;') + '</span>';
                    var mk1 = L.marker([first[0], first[1]], {{ icon: L.divIcon({{ className: 'line-end-label', html: labelHtml, iconSize: null, iconAnchor: [0, 0] }}) }});
                    var mk2 = L.marker([last[0], last[1]], {{ icon: L.divIcon({{ className: 'line-end-label', html: labelHtml, iconSize: null, iconAnchor: [0, 0] }}) }});
                    mk1.addTo(mapInstance);
                    mk2.addTo(mapInstance);
                    layerBucket.push(mk1, mk2);
                }}
            }});
        }}
        renderMapLinesOntoMap(linesData, lineLayers);
        window._mapLineLayers = lineLayers;
        window.refreshMapLines = function(newLinesData) {{
            if (!mapInstance) return;
            if (window._mapLineLayers) {{
                window._mapLineLayers.forEach(function(layer) {{
                    try {{ mapInstance.removeLayer(layer); }} catch (e) {{}}
                }});
            }}
            window._mapLineLayers = [];
            renderMapLinesOntoMap(newLinesData || [], window._mapLineLayers);
        }};
        
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
            
            function renderMapStationsOntoMap(stationsData, stationLayers) {{
                (stationsData || []).forEach(function(station) {{
                    var color = (station.under_construction === true) ? '#888888' : '#00ff00';
                    var label = (station.under_construction === true) ? ' (under construction)' : '';
                    var lineNames = station.line_names || [];
                    var lineLabel = lineNames.length ? ' (' + lineNames.join(', ') + ')' : '';
                    var labelStyle = 'font-size:9px;font-weight:bold;color:#333;text-shadow:0 0 2px #fff,0 0 2px #fff;white-space:nowrap;pointer-events:none;';
                    var nameAndLine = (station.name || '');
                    var isMerged = station.is_merged === true || (station.num_lines || 0) >= 2;
                    var dotSize = isMerged ? 16 : 12;
                    var borderStyle = isMerged ? '3px solid #0066cc' : '2px solid white';
                    var mergeRing = isMerged ? '<div style="position:absolute;left:-3px;top:-3px;width:' + (dotSize + 6) + 'px;height:' + (dotSize + 6) + 'px;border-radius:50%;border:2px dashed #0066cc;opacity:0.85;"></div>' : '';
                    var mergeBadge = isMerged ? '<span style="font-size:8px;color:#0066cc;margin-left:2px;">[' + (station.num_lines || lineNames.length) + ' lines]</span>' : '';
                    var markerHtml = '<div style="display:inline-block;vertical-align:middle;position:relative;">' + mergeRing + '<div style="background-color: ' + color + '; width: ' + dotSize + 'px; height: ' + dotSize + 'px; border-radius: 50%; border: ' + borderStyle + '; box-shadow: 0 0 4px rgba(0,0,0,0.5);"></div><span style="' + labelStyle + '">' + (nameAndLine.replace(/</g, '&lt;')) + mergeBadge + '</span></div>';
                    var stationMarker = L.marker([station.lat, station.lon], {{
                        icon: L.divIcon({{
                            className: 'station-marker' + (isMerged ? ' station-marker-merged' : ''),
                            html: markerHtml,
                            iconSize: null,
                            iconAnchor: [0, Math.floor(dotSize / 2)]
                        }})
                    }});
                    var encodedName = encodeURIComponent(station.name || '');
                    var closeBtnHtml = (station.can_close !== false) ? '<button class="close-station-btn" data-station-name="' + encodedName + '">Close Station</button>' : '';
                    var renameBtnHtml = (station.can_rename !== false && station.under_construction !== true) ? '<button class="rename-station-btn" data-station-name="' + encodedName + '" data-station-lat="' + station.lat + '" data-station-lon="' + station.lon + '">Rename Station</button>' : '';
                    var mergeNote = isMerged ? '<br/><i>Merged hub — ' + (station.num_lines || lineNames.length) + ' lines share this stop</i>' : '';
                    var stationPopupContent = '<div><b>Station: ' + (station.name || '') + label + '</b>' + (lineLabel ? '<br/>Line(s): ' + (station.line_names || []).join(', ') : '') + mergeNote + '<br/>' + renameBtnHtml + closeBtnHtml + '</div>';
                    stationMarker.bindPopup(stationPopupContent);
                    stationMarker.addTo(mapInstance);
                    stationLayers.push(stationMarker);
                }});
            }}
            var stationsData = {stations_json};
            var stationLayers = [];
            renderMapStationsOntoMap(stationsData, stationLayers);
            window._mapStationLayers = stationLayers;
            window.refreshMapStations = function(newStationsData) {{
                if (!mapInstance) return;
                if (window._mapStationLayers) {{
                    window._mapStationLayers.forEach(function(layer) {{
                        try {{ mapInstance.removeLayer(layer); }} catch (e) {{}}
                    }});
                }}
                window._mapStationLayers = [];
                renderMapStationsOntoMap(newStationsData || [], window._mapStationLayers);
            }};
            
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
                    var renameStationBtn = popupEl.querySelector('.rename-station-btn');
                    if (renameStationBtn) {{
                        renameStationBtn.addEventListener('click', function() {{
                            var name = renameStationBtn.getAttribute('data-station-name') || '';
                            var lat = renameStationBtn.getAttribute('data-station-lat') || '';
                            var lon = renameStationBtn.getAttribute('data-station-lon') || '';
                            window.renameStation(decodeURIComponent(name), parseFloat(lat), parseFloat(lon));
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

            window.renameStation = function(stationName, lat, lon) {{
                var event = new CustomEvent('mapRenameStation', {{
                    detail: {{ stationName: stationName, lat: lat, lon: lon }}
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
            var ROUTE_POINT_HIT_THRESHOLD = 0.0001;

            function findNearestRoutePointIndex(lat, lon, points) {{
                var minDist = Infinity;
                var nearestIndex = -1;
                for (var i = 0; i < points.length; i++) {{
                    var point = points[i];
                    var dist = Math.pow(point[0] - lat, 2) + Math.pow(point[1] - lon, 2);
                    if (dist < minDist) {{
                        minDist = dist;
                        nearestIndex = i;
                    }}
                }}
                if (nearestIndex >= 0 && minDist < ROUTE_POINT_HIT_THRESHOLD) {{
                    return nearestIndex;
                }}
                return -1;
            }}

            function removeLinePointAtIndex(index) {{
                clickedPoints.splice(index, 1);
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer._isLinePoint && layer._linePointIndex === index) {{
                        mapInstance.removeLayer(layer);
                    }} else if (layer instanceof L.Marker && layer._isLinePoint && layer._linePointIndex > index) {{
                        layer._linePointIndex--;
                    }}
                }});
                var event = new CustomEvent('mapPointClicked', {{
                    detail: {{ count: clickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}

            function addLinePoint(lat, lng) {{
                clickedPoints.push([lat, lng]);
                var marker = L.marker([lat, lng], {{
                    icon: L.divIcon({{
                        className: 'line-point-marker',
                        html: '<div style="background-color: #0066ff; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                        iconSize: [10, 10]
                    }})
                }}).addTo(mapInstance);
                marker._linePointIndex = clickedPoints.length - 1;
                marker._isLinePoint = true;
                marker.on('click', function(ev) {{
                    if (!isCreatingLine) return;
                    L.DomEvent.stopPropagation(ev);
                    removeLinePointAtIndex(marker._linePointIndex);
                }});
                var event = new CustomEvent('mapPointClicked', {{
                    detail: {{ count: clickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}

            function removeExtendPointAtIndex(index) {{
                extendClickedPoints.splice(index, 1);
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer._isExtendPoint && layer._extendPointIndex === index) {{
                        mapInstance.removeLayer(layer);
                    }} else if (layer instanceof L.Marker && layer._isExtendPoint && layer._extendPointIndex > index) {{
                        layer._extendPointIndex--;
                    }}
                }});
                updateExtendPreviewLine();
                var event = new CustomEvent('mapExtendPointClicked', {{
                    detail: {{ count: extendClickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}

            function addExtendPoint(lat, lng) {{
                extendClickedPoints.push([lat, lng]);
                var marker = L.marker([lat, lng], {{
                    icon: L.divIcon({{
                        className: 'extend-point-marker',
                        html: '<div style="background-color: #ff6600; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                        iconSize: [10, 10]
                    }})
                }}).addTo(mapInstance);
                marker._extendPointIndex = extendClickedPoints.length - 1;
                marker._isExtendPoint = true;
                marker.on('click', function(ev) {{
                    if (!isExtendingLine) return;
                    L.DomEvent.stopPropagation(ev);
                    removeExtendPointAtIndex(marker._extendPointIndex);
                }});
                updateExtendPreviewLine();
                var event = new CustomEvent('mapExtendPointClicked', {{
                    detail: {{ count: extendClickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}
            
            mapInstance.on('click', function(e) {{
                if (isCreatingLine) {{
                    addLinePoint(e.latlng.lat, e.latlng.lng);
                }} else if (isExtendingLine) {{
                    addExtendPoint(e.latlng.lat, e.latlng.lng);
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
                        var nearestIndex = findNearestRoutePointIndex(e.latlng.lat, e.latlng.lng, clickedPoints);
                        if (nearestIndex >= 0) {{
                            removeLinePointAtIndex(nearestIndex);
                        }}
                    }} else if (isExtendingLine) {{
                        var nearestIndex = findNearestRoutePointIndex(e.latlng.lat, e.latlng.lng, extendClickedPoints);
                        if (nearestIndex >= 0) {{
                            removeExtendPointAtIndex(nearestIndex);
                        }}
                    }}
                }}
            }});
            
            // Expose functions to Python via window object
            window.startLineCreation = function() {{
                isCreatingLine = true;
                clickedPoints = [];
            }};
            
            // Do not clear isCreatingLine here — Python cancels only after success or Cancel.
            // Returning a copy keeps points if the user backs out of line-type / elevation dialogs.
            window.finishLineCreation = function() {{
                return clickedPoints.slice();
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
                
                var existingPts = lineToExtend.points;
                var startExisting = existingPts[0];
                var endExisting = existingPts[existingPts.length - 1];
                var attachFromEnd = true;
                if (extendClickedPoints.length > 0) {{
                    var click = extendClickedPoints[0];
                    var dStart = Math.hypot(click[0] - startExisting[0], click[1] - startExisting[1]);
                    var dEnd = Math.hypot(click[0] - endExisting[0], click[1] - endExisting[1]);
                    attachFromEnd = dEnd <= dStart;
                }}
                
                // Build preview points: attach end of combined line + all extend points
                var previewPoints = [];
                if (attachFromEnd) {{
                    previewPoints.push([endExisting[0], endExisting[1]]);
                }} else {{
                    previewPoints.push([startExisting[0], startExisting[1]]);
                }}
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
            
            // Keep extension mode until Python confirms success or Cancel.
            window.finishLineExtension = function() {{
                return extendClickedPoints.slice();
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

            window.isSellingLineSegment = false;
            window.segmentSellLineName = null;
            window.segmentSellClicks = [];
            window.segmentSellMarkers = [];

            function clearSegmentSellMarkers() {{
                (window.segmentSellMarkers || []).forEach(function(m) {{
                    try {{ mapInstance.removeLayer(m); }} catch (e) {{}}
                }});
                window.segmentSellMarkers = [];
            }}

            function addSegmentSellClick(lat, lng) {{
                if (!isSellingLineSegment || !segmentSellLineName) return;
                if (segmentSellClicks.length >= 2) return;
                segmentSellClicks.push([lat, lng]);
                var marker = L.marker([lat, lng], {{
                    icon: L.divIcon({{
                        className: 'segment-sell-marker',
                        html: '<div style="background-color: #cc0000; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                        iconSize: [10, 10]
                    }})
                }}).addTo(mapInstance);
                window.segmentSellMarkers.push(marker);
                var event = new CustomEvent('mapSegmentSellClicked', {{
                    detail: {{ count: segmentSellClicks.length }}
                }});
                window.dispatchEvent(event);
                if (segmentSellClicks.length >= 2) {{
                    window.pendingSegmentSell = JSON.stringify({{
                        lineName: segmentSellLineName,
                        points: segmentSellClicks.slice()
                    }});
                }}
            }}

            window.startLineSegmentSell = function(lineName) {{
                isSellingLineSegment = true;
                segmentSellLineName = lineName;
                segmentSellClicks = [];
                clearSegmentSellMarkers();
                window.pendingSegmentSell = null;
            }};

            window.cancelLineSegmentSell = function() {{
                isSellingLineSegment = false;
                segmentSellLineName = null;
                segmentSellClicks = [];
                clearSegmentSellMarkers();
                window.pendingSegmentSell = null;
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
            // Invalidate size when window becomes visible (for tab switching)
            document.addEventListener('visibilitychange', function() {{
                var m = window.mapInstance;
                if (m && !document.hidden) {{
                    if (typeof window.ensureMapVisible === 'function') {{
                        window.ensureMapVisible();
                    }} else {{
                        ensureMapContainerSize();
                        setTimeout(function() {{ m.invalidateSize(true); }}, 100);
                    }}
                }}
            }});
            window.addEventListener('resize', function() {{
                if (typeof window.ensureMapVisible === 'function') {{
                    window.ensureMapVisible();
                }} else {{
                    ensureMapContainerSize();
                    var m = window.mapInstance;
                    if (m) {{
                        setTimeout(function() {{ m.invalidateSize(true); }}, 50);
                    }}
                }}
            }});
        }}
        document.addEventListener('DOMContentLoaded', initMap);
        initMap();
    </script>
</body>
</html>"""
        
        QApplication.processEvents()
        return html

    def DisplayFinanceTab(self):
        """Update Finance tab for all players: current profit/expenses only (AI finances are in AI Watch tab)."""
        if not self.operations or not hasattr(self, 'player_tabs_dict') or not self.player_tabs_dict:
            return
        year = self.operations.get_year()
        first_key = next(iter(self.player_tabs_dict.keys()))
        first_value = self.player_tabs_dict[first_key]
        if not isinstance(first_value, dict):
            return
        for player_id, player_tabs_dict in self.player_tabs_dict.items():
            finance_text_widget = player_tabs_dict.get('finance_text')
            if not finance_text_widget:
                continue
            players = self.operations.get_players()
            player = next((p for p in players if self.operations.get_player_id(p) == player_id), None)
            if not player:
                continue
            try:
                s = self.operations.get_player_financial_summary(player, year)
            except Exception:
                s = {}
            lines = ["——— Your finances (this year) ———"]
            lines.append(f"Income:")
            lines.append(f"  Line profit:        ${s.get('profit', 0):,}")
            lines.append(f"  Government grant:  ${s.get('government_grant', 0):,}")
            lines.append(f"Expenses:")
            lines.append(f"  Trainset maintenance: ${s.get('trainset_maintenance', 0):,}")
            lines.append(f"  Resource charge:     ${s.get('resource_charge', 0):,}")
            lines.append(f"  Line maintenance:    ${s.get('line_maintenance', 0):,}")
            lines.append(f"  Poll tax:            ${s.get('poll_tax', 0):,}")
            lines.append(f"  Property tax:        ${s.get('property_tax', 0):,}")
            lines.append(f"  Income tax:          ${s.get('income_tax', 0):,}")
            lines.append(f"  Worker costs:        ${s.get('worker_costs', 0):,}")
            lines.append(f"Net (this year):      ${s.get('net', 0):,}")
            # Next year prediction (no new laws — uses current rules only)
            try:
                next_year = year + 1
                s_next = self.operations.get_player_financial_summary(player, next_year)
                lines.append("")
                lines.append("——— Next year (predicted, no new laws) ———")
                lines.append(f"Income:")
                lines.append(f"  Line profit:        ${s_next.get('profit', 0):,}")
                lines.append(f"  Government grant:  ${s_next.get('government_grant', 0):,}")
                lines.append(f"Expenses:")
                lines.append(f"  Trainset maintenance: ${s_next.get('trainset_maintenance', 0):,}")
                lines.append(f"  Resource charge:     ${s_next.get('resource_charge', 0):,}")
                lines.append(f"  Line maintenance:    ${s_next.get('line_maintenance', 0):,}")
                lines.append(f"  Poll tax:            ${s_next.get('poll_tax', 0):,}")
                lines.append(f"  Property tax:        ${s_next.get('property_tax', 0):,}")
                lines.append(f"  Income tax:          ${s_next.get('income_tax', 0):,}")
                lines.append(f"  Worker costs:        ${s_next.get('worker_costs', 0):,}")
                lines.append(f"Net (predicted):      ${s_next.get('net', 0):,}")
            except Exception:
                pass
            finance_text_widget.setText("\n".join(lines))

    def DisplayAIWatchFinances(self):
        """Update AI Watch tab's finances section (all years money up/down)."""
        if not self.operations or not getattr(self, 'ai_watch_enabled', False):
            return
        if not hasattr(self, 'ai_watch_finances_down_text') or not self.ai_watch_finances_down_text:
            return
        ai_summary = self.operations.get_ai_money_debug_summary()
        down_lines = []
        up_lines = []
        for e in ai_summary.get('downs', []):
            down_lines.append(
                f"Year {e['year']}  {e['player_name']}: -${e['amount']:,}  ({e['reason']})  Balance: ${e['balance_after']:,}"
            )
        for e in ai_summary.get('ups', []):
            up_lines.append(
                f"Year {e['year']}  {e['player_name']}: +${e['amount']:,}  ({e['reason']})  Balance: ${e['balance_after']:,}"
            )
        if not down_lines:
            down_lines.append("(No money-down entries yet.)")
        if not up_lines:
            up_lines.append("(No money-up entries yet.)")
        self.ai_watch_finances_down_text.setPlainText("\n".join(down_lines))
        if hasattr(self, 'ai_watch_finances_up_text') and self.ai_watch_finances_up_text:
            self.ai_watch_finances_up_text.setPlainText("\n".join(up_lines))

    def _fare_slider_quarter_bounds(self) -> tuple[int, int]:
        """Slider range in $0.25 steps: $2.00 minimum, $15.00 maximum."""
        from inflation_utils import FARE_QUARTER_DOLLARS
        min_quarters = 8
        max_quarters = int(round(15.0 / FARE_QUARTER_DOLLARS))
        return min_quarters, max_quarters

    def _add_line_fare_controls(self, line, layout) -> None:
        from inflation_utils import FARE_QUARTER_DOLLARS, format_fare_display

        min_fare_quarters, max_fare_quarters = self._fare_slider_quarter_bounds()
        current_fare = self.operations.get_line_fare(line)
        current_quarters = int(round(current_fare / FARE_QUARTER_DOLLARS))
        current_quarters = max(min_fare_quarters, min(max_fare_quarters, current_quarters))

        layout.addWidget(QLabel("Fare:"))
        fare_slider = QSlider(Qt.Orientation.Horizontal)
        fare_slider.setMinimum(min_fare_quarters)
        fare_slider.setMaximum(max_fare_quarters)
        fare_slider.setValue(current_quarters)
        fare_slider.setSingleStep(1)
        fare_slider.setPageStep(4)
        fare_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        fare_slider.setTickInterval(4)
        fare_slider.setMinimumWidth(200)

        fare_label = QLabel(format_fare_display(current_fare))
        fare_label.setMinimumWidth(48)

        def update_fare(quarters, line_obj=line, label=fare_label, slider=fare_slider):
            fare = quarters * FARE_QUARTER_DOLLARS
            self.operations.set_line_fare(line_obj, fare)
            label.setText(format_fare_display(fare))

        fare_slider.valueChanged.connect(update_fare)
        layout.addWidget(fare_slider)
        layout.addWidget(fare_label)
    
    def DisplayLinesControls(self, player=None):
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        data_player = self.operations.get_effective_player(player) or player
        lines_layout = self.player_tabs_dict[player_id].get('lines_layout')
        lines_scroll_layout = self.player_tabs_dict[player_id].get('lines_scroll_layout')
        if lines_layout is None or lines_scroll_layout is None:
            return
        try:
            _ = lines_layout.count()
            _ = lines_scroll_layout.count()
        except RuntimeError:
            return
        self._clear_layout_widgets(lines_scroll_layout)
        if not any(isinstance(w, QPushButton) and w.text() == "Refresh" for i in range(lines_layout.count()) for w in [lines_layout.itemAt(i).widget()] if w):
            refresh_btn = QPushButton("Refresh")
            def refresh_with_player():
                self.DisplayLinesControls(player)
            refresh_btn.clicked.connect(refresh_with_player)
            lines_layout.insertWidget(0, refresh_btn)
        scroll_layout = lines_scroll_layout
        
        line_names = self.operations.get_player_line_names(data_player)
        build_queue = self.operations.get_build_queue()
        player_id = self.operations.get_player_id(player)
        
        construction_line_names = set()
        for build in build_queue:
            if build.get('type') == 'line' and build.get('player'):
                if self.operations.get_effective_player(build.get('player')) == data_player:
                    line_name = build.get('data', {}).get('name')
                    if line_name:
                        construction_line_names.add(line_name)
        
        all_line_names = set(line_names) if line_names else set()
        all_line_names.update(construction_line_names)
        
        for_other = self.player_tabs_dict[player_id].get('for_other_player', False)
        if not all_line_names:
            empty_msg = "No lines owned." if for_other else "No available lines"
            scroll_layout.addWidget(QLabel(empty_msg))
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
                            if self.operations.get_effective_player(build.get('player')) == data_player:
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
                
                # Other players tab: read-only (name, stations, profit) — no fare sliders or edit buttons
                if for_other:
                    num_stations = len(self.operations.get_line_stations(line))
                    try:
                        profit = self.operations.calc_line_profit(line, self.operations.get_year())
                    except Exception:
                        profit = 0
                    line_layout = QHBoxLayout()
                    line_layout.addWidget(QLabel(f"  {line_name}: stations={num_stations}, profit=${int(profit):,}"))
                    line_widget = QWidget()
                    line_widget.setLayout(line_layout)
                    scroll_layout.addWidget(line_widget)
                    continue
                
                line_layout = QHBoxLayout()
                line_layout.addWidget(QLabel(f"  {line_name}:"))

                self._add_line_fare_controls(line, line_layout)
                
                frequency = self.operations.get_line_frequency(line)
                frequency_label = QLabel(f"Frequency: {frequency}/year")
                frequency_label.setMinimumWidth(120)
                line_layout.addWidget(frequency_label)
                upgrade_btn = QPushButton("Upgrade")
                def make_upgrade_handler(l, p):
                    return lambda: self.on_upgrade_line_for_line(l, p)
                upgrade_btn.clicked.connect(make_upgrade_handler(line, data_player))
                line_layout.addWidget(upgrade_btn)
                edit_btn = QPushButton("Edit Line")
                def make_edit_handler(l, p):
                    return lambda: self.on_edit_line_clicked(l, p)
                edit_btn.clicked.connect(make_edit_handler(line, data_player))
                line_layout.addWidget(edit_btn)
                
                line_widget = QWidget()
                line_widget.setLayout(line_layout)
                scroll_layout.addWidget(line_widget)
        
    
    def DisplayRollingStockInfo(self, player=None):
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        data_player = self.operations.get_effective_player(player) or player
        try:
            rolling_stock_trainsets_layout = self.player_tabs_dict[player_id]['rolling_stock_trainsets_layout']
            rolling_stock_locomotives_layout = self.player_tabs_dict[player_id]['rolling_stock_locomotives_layout']
            rolling_stock_railcars_layout = self.player_tabs_dict[player_id]['rolling_stock_railcars_layout']
        except RuntimeError:
            return
        if not rolling_stock_trainsets_layout:
            return
        try:
            self._display_rolling_stock_impl(data_player, rolling_stock_trainsets_layout, rolling_stock_locomotives_layout, rolling_stock_railcars_layout, player_id)
        except RuntimeError:
            return

    def DisplayRailcarsInfo(self, player=None):
        """Refresh only the railcars list (faster than full rolling stock rebuild)."""
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        data_player = self.operations.get_effective_player(player) or player
        player_tabs_dict = self.player_tabs_dict[player_id]
        inner_layout = player_tabs_dict.get('railcars_inner_layout')
        if inner_layout is None:
            self.DisplayRollingStockInfo(player)
            return
        try:
            self._populate_railcars_layout(inner_layout, data_player)
        except RuntimeError:
            return

    def _clear_layout_widgets(self, layout):
        while layout.count():
            child = layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.deleteLater()

    def _populate_railcars_layout(self, railcars_layout, player):
        self._clear_layout_widgets(railcars_layout)
        railcars = getattr(player, 'railcars', []) or []
        if railcars:
            for i, car in enumerate(railcars):
                car_name = getattr(car, 'name', f'Railcar {i+1}')
                cap = getattr(car, 'capacity', 0)
                railcar_layout = QHBoxLayout()
                railcar_layout.addWidget(QLabel(f"  {car_name} (Capacity: {cap})"))
                assign_btn = QPushButton("Assign to Trainset")
                def make_assign_handler(c, p):
                    return lambda: self.on_assign_railcar_to_trainset(c, p)
                assign_btn.clicked.connect(make_assign_handler(car, player))
                railcar_layout.addWidget(assign_btn)
                sell_btn = QPushButton("Sell")
                def make_sell_handler(c, p):
                    return lambda: self.on_sell_railcar_from_rolling_stock(c, p)
                sell_btn.clicked.connect(make_sell_handler(car, player))
                railcar_layout.addWidget(sell_btn)
                railcar_layout.addStretch()
                railcar_widget = QWidget()
                railcar_widget.setLayout(railcar_layout)
                railcars_layout.addWidget(railcar_widget)
        else:
            railcars_layout.addWidget(QLabel("  No railcars"))

    def _display_rolling_stock_impl(self, player, rolling_stock_trainsets_layout, rolling_stock_locomotives_layout, rolling_stock_railcars_layout, player_id=None):
        """Implementation of DisplayRollingStockInfo - separated for try/except scope."""
        player_tabs_dict = self.player_tabs_dict.get(player_id, {}) if player_id is not None else {}
        trainsets_content_layout = player_tabs_dict.get('rolling_stock_trainsets_content_layout')
        locomotives_content_layout = player_tabs_dict.get('rolling_stock_locomotives_content_layout')
        railcars_content_layout = player_tabs_dict.get('rolling_stock_railcars_content_layout') or player_tabs_dict.get('railcars_inner_layout')
        if trainsets_content_layout is None or locomotives_content_layout is None or railcars_content_layout is None:
            return
        self._clear_layout_widgets(trainsets_content_layout)
        self._clear_layout_widgets(locomotives_content_layout)
        self._clear_layout_widgets(railcars_content_layout)
        if player_id is not None:
            self.player_tabs_dict[player_id]['railcars_inner_layout'] = railcars_content_layout
        
        if not any(isinstance(w, QPushButton) and w.text() == "Refresh Rolling Stock" for i in range(rolling_stock_trainsets_layout.count()) for w in [rolling_stock_trainsets_layout.itemAt(i).widget()] if w):
            refresh_btn = QPushButton("Refresh Rolling Stock")
            def refresh_with_player():
                self.DisplayRollingStockInfo(player)
            refresh_btn.clicked.connect(refresh_with_player)
            rolling_stock_trainsets_layout.insertWidget(0, refresh_btn)
        
        trainsets_layout = trainsets_content_layout
        locomotives_layout = locomotives_content_layout
        railcars_layout = railcars_content_layout
        
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
                    if line_assignment:
                        unassign_btn = QPushButton("Unassign")
                        def make_unassign_handler(t, p):
                            return lambda: self.on_unassign_trainset_for_trainset(t, p)
                        unassign_btn.clicked.connect(make_unassign_handler(ts, player))
                        trainset_layout.addWidget(unassign_btn)
                        change_line_btn = QPushButton("Change Line")
                        def make_change_handler(t, p):
                            return lambda: self.on_change_trainset_line(t, p)
                        change_line_btn.clicked.connect(make_change_handler(ts, player))
                        trainset_layout.addWidget(change_line_btn)
                    else:
                        assign_btn = QPushButton("Assign to Line")
                        def make_assign_handler(t, p):
                            return lambda: self.on_assign_trainset_for_trainset(t, p)
                        assign_btn.clicked.connect(make_assign_handler(ts, player))
                        trainset_layout.addWidget(assign_btn)
                    modify_btn = QPushButton("Modify")
                    def make_modify_handler(t, p):
                        return lambda: self.on_modify_trainset_for_trainset(t, p)
                    modify_btn.clicked.connect(make_modify_handler(ts, player))
                    trainset_layout.addWidget(modify_btn)
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
        regular_locomotives = []
        self_contained_locomotives = []
        for loco in locomotives:
            if _locomotive_is_self_contained(loco):
                self_contained_locomotives.append(loco)
            else:
                regular_locomotives.append(loco)
        if self_contained_locomotives:
            for i, loco in enumerate(self_contained_locomotives):
                loco_name = getattr(loco, 'name', f'Unit {i+1}')
                loco_layout = QHBoxLayout()
                loco_label = QLabel(f"  {loco_name} (self-contained trainset)")
                loco_layout.addWidget(loco_label)
                create_ts_btn = QPushButton("Create Trainset")
                def make_create_handler(l, p):
                    return lambda: self.on_create_trainset_from_locomotive(l, p)
                create_ts_btn.clicked.connect(make_create_handler(loco, player))
                loco_layout.addWidget(create_ts_btn)
                sell_btn = QPushButton("Sell")
                def make_sell_handler(l, p):
                    return lambda: self.on_sell_locomotive_from_rolling_stock(l, p)
                sell_btn.clicked.connect(make_sell_handler(loco, player))
                loco_layout.addWidget(sell_btn)
                loco_layout.addStretch()
                loco_widget = QWidget()
                loco_widget.setLayout(loco_layout)
                trainsets_section.addWidget(loco_widget)
        if regular_locomotives:
            for i, loco in enumerate(regular_locomotives):
                loco_name = getattr(loco, 'name', f'Locomotive {i+1}')
                loco_layout = QHBoxLayout()
                loco_label = QLabel(f"  {loco_name}")
                loco_layout.addWidget(loco_label)
                create_ts_btn = QPushButton("Create Trainset")
                def make_create_handler(l, p):
                    return lambda: self.on_create_trainset_from_locomotive(l, p)
                create_ts_btn.clicked.connect(make_create_handler(loco, player))
                loco_layout.addWidget(create_ts_btn)
                sell_btn = QPushButton("Sell")
                def make_sell_handler(l, p):
                    return lambda: self.on_sell_locomotive_from_rolling_stock(l, p)
                sell_btn.clicked.connect(make_sell_handler(loco, player))
                loco_layout.addWidget(sell_btn)
                loco_layout.addStretch()
                loco_widget = QWidget()
                loco_widget.setLayout(loco_layout)
                locomotives_section.addWidget(loco_widget)
        elif not self_contained_locomotives:
            locomotives_section.addWidget(QLabel("  No locomotives"))

        locomotives_w = QWidget()
        locomotives_w.setLayout(locomotives_section)
        locomotives_layout.addWidget(locomotives_w)
        
        self._populate_railcars_layout(railcars_layout, player)
    
    def DisplayWorkersControls(self, player=None):
        if player is None:
            player = self.operations.get_current_player(self.current_player_index)
        if player is None:
            return
        player_id = self.operations.get_player_id(player)
        if player_id not in self.player_tabs_dict:
            return
        data_player = self.operations.get_effective_player(player) or player
        workers_layout = self.player_tabs_dict[player_id]['workers_layout']
        workers_scroll_layout = self.player_tabs_dict[player_id].get('workers_scroll_layout')
        if not workers_layout or workers_scroll_layout is None:
            return
        try:
            _ = workers_layout.count()
            _ = workers_scroll_layout.count()
        except RuntimeError:
            return
        self._clear_layout_widgets(workers_scroll_layout)
        if not any(isinstance(w, QPushButton) and w.text() == "Refresh Workers Controls" for i in range(workers_layout.count()) for w in [workers_layout.itemAt(i).widget()] if w):
            refresh_btn = QPushButton("Refresh Workers Controls")
            def refresh_with_player():
                self.DisplayWorkersControls(player)
            refresh_btn.clicked.connect(refresh_with_player)
            workers_layout.insertWidget(1, refresh_btn)
        
        scroll_layout = workers_scroll_layout
        
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
        
        engineer_rental = self.operations.get_player_worker_rental(data_player, ENGINEER.name)
        if engineer_rental:
            engineer_widget = create_worker_salary_control(ENGINEER, engineer_rental, data_player)
            if engineer_widget:
                worker_section.addWidget(engineer_widget)
        
        construction_rental = self.operations.get_player_worker_rental(data_player, CONSTRUCTION_WORKER.name)
        if construction_rental:
            construction_widget = create_worker_salary_control(CONSTRUCTION_WORKER, construction_rental, data_player)
            if construction_widget:
                worker_section.addWidget(construction_widget)
        
        lawyer_rental = self.operations.get_player_worker_rental(data_player, LAWYER.name)
        if lawyer_rental:
            lawyer_widget = create_worker_salary_control(LAWYER, lawyer_rental, data_player)
            if lawyer_widget:
                worker_section.addWidget(lawyer_widget)
        
        maintenance_rental = self.operations.get_player_worker_rental(data_player, MAINTENANCE_WORKER.name)
        if maintenance_rental:
            maintenance_widget = create_worker_salary_control(MAINTENANCE_WORKER, maintenance_rental, data_player)
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
        try:
            _ = law_layout.count()
        except RuntimeError:
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
        
        # Use effective player for content so merged humans see the same company (lawyers, lawsuits)
        display_player = self.operations.get_effective_player(player)
        
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_widget.setLayout(scroll_layout)
        
        try:
            from lawsuit import LAWSUIT_EXCUSES
            from worker import LAWYER
            
            try:
                player_section = QVBoxLayout()
                
                lawyer_count = self.operations.get_player_lawyer_count(display_player)
                available = self.operations.get_player_available_lawyer_count(display_player)
                lawyer_label = QLabel(f"  Lawyers: {lawyer_count} total, {available} available for lawsuits (rest drafting laws)")
                player_section.addWidget(lawyer_label)
                
                excuses = self.operations.get_player_lawsuit_excuses(display_player)
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
                
                # Law drafting section
                draft_options = self.operations.get_law_draft_options(display_player)
                in_progress, ready_list = self.operations.get_player_drafts(display_player)
                player_section.addWidget(QLabel("--- Law drafting (lawyers draft; occupied lawyers cannot fight lawsuits) ---"))
                if in_progress:
                    player_section.addWidget(QLabel(f"  Drafting: {len(in_progress)} law(s) in progress (ready next year)"))
                if ready_list:
                    for item in ready_list:
                        law_type = item[0]
                        lbl = next((o['label'] for o in draft_options if o['id'] == law_type), law_type)
                        row = QVBoxLayout()
                        row.addWidget(QLabel(f"  Ready: {lbl}"))
                        opts = {o['id']: o for o in draft_options}
                        req_target = opts.get(law_type, {}).get('requires_target', False)
                        target_combo = QComboBox()
                        for p in self.operations.get_active_players():
                            if p != display_player:
                                target_combo.addItem(self.operations.get_player_name(p), p)
                        draft_targets = item[3] if len(item) > 3 else None
                        if draft_targets:
                            tgt_names = ", ".join(self.operations.get_player_name(p) for p in draft_targets if p)
                            row.addWidget(QLabel(f"  Targets: {tgt_names}"))
                        if req_target:
                            row.addWidget(QLabel("Target:"))
                            row.addWidget(target_combo)
                        sub_btn = QPushButton("Submit to Committee")
                        def make_submit(p, lt, need_tgt, tgt_combo):
                            def submit():
                                target = tgt_combo.currentData() if need_tgt else None
                                r = self.operations.submit_law_draft(p, lt, target)
                                QMessageBox.information(self, "Law Submission", r['message'])
                                self.DisplayLawInfo(p)
                                self.DisplayGovernmentInfo()
                            return submit
                        sub_btn.clicked.connect(make_submit(display_player, law_type, req_target, target_combo))
                        row.addWidget(sub_btn)
                        w = QWidget()
                        w.setLayout(row)
                        player_section.addWidget(w)
                draft_row = QVBoxLayout()
                draft_top = QHBoxLayout()
                draft_combo = QComboBox()
                for o in draft_options:
                    draft_combo.addItem(o['label'], o['id'])
                lawyer_spin = QSpinBox()
                lawyer_spin.setMinimum(1)
                lawyer_spin.setMaximum(max(1, display_player.get_lawyer_count()))
                lawyer_spin.setValue(1)
                lawyer_spin.setToolTip("More lawyers = higher success chance when submitting")
                draft_top.addWidget(QLabel("Draft law:"))
                draft_top.addWidget(draft_combo)
                draft_top.addWidget(QLabel("Lawyers:"))
                draft_top.addWidget(lawyer_spin)
                draft_row.addLayout(draft_top)
                target_checks_container = QWidget()
                target_checks_layout = QHBoxLayout()
                target_checks_layout.addWidget(QLabel("Targets (game calculates who benefits; their seats help pass):"))
                target_checkboxes = {}
                for p in self.operations.get_active_players():
                    cb = QCheckBox(self.operations.get_player_name(p))
                    cb.setChecked(p == display_player)
                    target_checkboxes[id(p)] = (p, cb)
                    target_checks_layout.addWidget(cb)
                target_checks_container.setLayout(target_checks_layout)
                def on_law_type_changed():
                    lt = draft_combo.currentData()
                    opts = {o['id']: o for o in draft_options}
                    target_checks_container.setVisible(opts.get(lt, {}).get('allows_multi_target', False))
                draft_combo.currentIndexChanged.connect(on_law_type_changed)
                on_law_type_changed()
                draft_row.addWidget(target_checks_container)
                def on_start_draft():
                    lt = draft_combo.currentData()
                    num = lawyer_spin.value()
                    opts = {o['id']: o for o in draft_options}
                    tgt_players = None
                    if opts.get(lt, {}).get('allows_multi_target', False):
                        tgt_players = [pl for pl, cb in target_checkboxes.values() if cb.isChecked()]
                        if not tgt_players:
                            tgt_players = [display_player]
                    r = self.operations.start_law_draft(display_player, lt, num_lawyers=num, target_players=tgt_players)
                    QMessageBox.information(self, "Law Draft", r['message'])
                    self.DisplayLawInfo(player)
                start_btn = QPushButton("Start Draft (1 year)")
                start_btn.clicked.connect(on_start_draft)
                draft_row.addWidget(start_btn)
                dw = QWidget()
                dw.setLayout(draft_row)
                player_section.addWidget(dw)
                
                player_section.addWidget(QLabel("--- File lawsuit ---"))
                file_btn = QPushButton(f"File Lawsuit ({self.operations.get_player_name(display_player)})")
                def make_file_handler(p):
                    return lambda: self.on_file_lawsuit_clicked(p)
                file_btn.clicked.connect(make_file_handler(display_player))
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

            result = self.operations.try_file_lawsuit(plaintiff, defendant, selected_excuse, made_up_cause)
            if not result.get('success'):
                QMessageBox.warning(dlg, "Error", result.get('message', 'Unknown error.'))
                return

            r = result
            QMessageBox.warning(
                self,
                f"You've been sued by {r['plaintiff_name']}!",
                f"{r['defendant_name']}, you have been sued by {r['plaintiff_name']}!\n\n"
                f"Cause: {r['cause']}\n"
                f"Damages sought: ${r['damages']}\n\n"
                f"Your lawyers: {r['defendant_lawyers']}\n"
                f"{r['plaintiff_name']}'s lawyers: {r['plaintiff_lawyers']}"
            )
            QMessageBox.information(self, "Lawsuit Result", r['result_message'])
            dlg.close()

            self.DisplayLawInfo(plaintiff)
            self.DisplayMarketplaceInfo()
            self.DisplayLinesControls(plaintiff)
            self.DisplayWorkersControls(plaintiff)

        file_btn.clicked.connect(do_file)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def on_government_lobby_clicked(self, player):
        """Open lobby dialog for government seats only (influence_seat, influence_tiebreaker)."""
        self._open_lobby_dialog(player, self.operations.get_government_lobby_options(player))

    def on_lobby_clicked(self, player):
        """Open full lobby dialog (all options). Kept for any remaining uses."""
        self._open_lobby_dialog(player, self.operations.get_lobby_options(player))

    def _open_lobby_dialog(self, player, options):
        """Open lobby dialog with given options. Used for government (seats) and legacy full lobby."""
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
        target_checks_widget = QWidget()
        target_checks_layout = QHBoxLayout()
        target_checks_layout.addWidget(QLabel("Target(s):"))
        target_checkboxes = {}
        others = [p for p in self.operations.get_active_players() if p != player]
        for p in others:
            target_combo.addItem(self.operations.get_player_name(p), p)
            cb = QCheckBox(self.operations.get_player_name(p))
            target_checkboxes[id(p)] = (p, cb)
            target_checks_layout.addWidget(cb)
        target_checks_widget.setLayout(target_checks_layout)
        layout.addWidget(target_checks_widget)
        def toggle_target(ix):
            o = option_combo.itemData(ix)
            need = o and o.get('requires_target')
            multi = o and o.get('allows_multi_target', False)
            target_label.setVisible(need and not multi)
            target_combo.setVisible(need and not multi)
            target_checks_widget.setVisible(need and multi)
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
            o = option_combo.currentData()
            opt_id = o['id'] if o else 'drop_excuse'
            chance = self.operations.get_lobby_success_chance(amt, opt_id)
            chance_label.setText(f"Estimated success chance: {chance * 100:.0f}%")
        amount_spin.valueChanged.connect(update_chance)
        option_combo.currentIndexChanged.connect(update_chance)
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
            tgt_players = [pl for pl, cb in target_checkboxes.values() if cb.isChecked()] if target_checks_widget.isVisible() else None
            result = self.operations.try_lobby(player, amt, option_id=option_id, target_player=target, target_players=tgt_players)
            dlg.close()
            self.update_status_bar()
            title = "Lobbying Succeeded" if result['success'] else "Lobbying Failed"
            QMessageBox.information(self, title, result['message'])
            if hasattr(self, 'DisplayGovernmentInfo'):
                self.DisplayGovernmentInfo(player)

        ok_btn.clicked.connect(do_lobby)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def _open_lobby_seat_dialog(self, player, seat_index: int):
        """Open lobby dialog to lobby a specific seat."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Lobby Seat")
        dlg.setGeometry(200, 200, 400, 200)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Pay money to influence this seat to be favorable to you."))
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
            chance = self.operations.get_lobby_success_chance(amount_spin.value(), 'influence_seat')
            chance_label.setText(f"Estimated success chance: {chance * 100:.0f}%")
        amount_spin.valueChanged.connect(update_chance)
        update_chance()
        layout.addWidget(chance_label)
        layout.addWidget(QLabel(f"Your money: ${self.operations.get_player_money(player)}"))
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Lobby")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)

        def do_lobby():
            result = self.operations.try_lobby(player, amount_spin.value(), option_id='influence_seat', seat_index=seat_index)
            dlg.close()
            self.update_status_bar()
            QMessageBox.information(self, "Lobbying Succeeded" if result['success'] else "Lobbying Failed", result['message'])
            if hasattr(self, 'DisplayGovernmentInfo'):
                self.DisplayGovernmentInfo(player)
        ok_btn.clicked.connect(do_lobby)
        cancel_btn.clicked.connect(dlg.close)
        dlg.exec()

    def _open_lobby_leader_dialog(self, player):
        """Open lobby dialog to lobby the leader (tiebreaker)."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Lobby Leader")
        dlg.setGeometry(200, 200, 400, 200)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Pay money to influence the leader to be favorable to you."))
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
            chance = self.operations.get_lobby_success_chance(amount_spin.value(), 'influence_tiebreaker')
            chance_label.setText(f"Estimated success chance: {chance * 100:.0f}%")
        amount_spin.valueChanged.connect(update_chance)
        update_chance()
        layout.addWidget(chance_label)
        layout.addWidget(QLabel(f"Your money: ${self.operations.get_player_money(player)}"))
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Lobby")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        dlg.setLayout(layout)

        def do_lobby():
            result = self.operations.try_lobby(player, amount_spin.value(), option_id='influence_tiebreaker')
            dlg.close()
            self.update_status_bar()
            QMessageBox.information(self, "Lobbying Succeeded" if result['success'] else "Lobbying Failed", result['message'])
            if hasattr(self, 'DisplayGovernmentInfo'):
                self.DisplayGovernmentInfo(player)
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
        
        for player_id, player_tabs_dict in list(self.player_tabs_dict.items()):
            in_progress_layout = player_tabs_dict.get('in_progress_layout')
            complete_jobs_layout = player_tabs_dict.get('complete_jobs_layout')
            for_other_player = player_tabs_dict.get('for_other_player', False)
            
            if in_progress_layout is None or complete_jobs_layout is None:
                continue
            try:
                # Clear existing widgets (layout may be deleted if tabs were rebuilt)
                _ = in_progress_layout.count()
            except RuntimeError:
                continue
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
            effective = self.operations.get_effective_player(player)
            # Show build queue for the company (effective player); merged humans see same jobs
            player_builds = [idx for idx, build in enumerate(build_queue) 
                           if build.get('player') and self.operations.get_effective_player(build.get('player')) == effective]
            
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
                
                if not for_other_player:
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
                
                if not for_other_player:
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
                    
                    if job_type == 'line' and not for_other_player:
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
                                result = self.operations.sell_player_line_to_marketplace(current_player, line_name)
                                if result.get('listed'):
                                    QMessageBox.information(self, "Line Sold", f"Line '{line_name}' has been listed on the marketplace.")
                                else:
                                    QMessageBox.information(self, "Line Removed", f"Pending work on '{line_name}' was cancelled.")
                                self.refresh_construction_tab()
                                self.DisplayMapInfo()
                                self.DisplayLinesControls(current_player)
                                self.DisplayMarketplaceInfo()
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


# --- Networked extensions ---

#!/usr/bin/env python3
"""
Networked version of TransitGame UI
Merges qt.py with networking functionality
"""
import sys
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import (QMessageBox, QCheckBox, QLineEdit, QLabel, 
                             QDialog, QVBoxLayout, QPushButton, QHBoxLayout,
                             QTabWidget, QWidget)
from PyQt6.QtGui import QColor, QPalette
try:
    from network_client import NetworkClient, NetworkThread
    from message_protocol import parse_server_address
    NETWORKING_AVAILABLE = True
except ImportError:
    NETWORKING_AVAILABLE = False
    parse_server_address = None
    logger.warning("Networking not available. Install aiohttp and python-socketio to enable multiplayer.")


class RulesetEditorDialog(QDialog):
    """Create and edit rulesets: RULES.md, VEHICLES.md, vehicles.json, events.json, news.json, organizations.json."""

    def __init__(self, parent=None, initial_tab: int = 0):
        super().__init__(parent)
        from ruleset_loader import ensure_rulesets_layout, get_active_ruleset
        ensure_rulesets_layout()
        apply_application_logo(self)
        self.setWindowTitle("Ruleset Editor")
        self.setMinimumSize(760, 560)
        self._ruleset_name = get_active_ruleset()
        self._baseline = None
        self._loading = False
        root = QVBoxLayout()
        self.setLayout(root)

        top = QHBoxLayout()
        top.addWidget(QLabel("Ruleset:"))
        self.ruleset_combo = QComboBox()
        self.ruleset_combo.currentIndexChanged.connect(self._on_ruleset_combo_index_changed)
        top.addWidget(self.ruleset_combo, 1)
        new_btn = QPushButton("New…")
        new_btn.clicked.connect(self._on_new_ruleset)
        dup_btn = QPushButton("Duplicate…")
        dup_btn.clicked.connect(self._on_duplicate_ruleset)
        self.delete_btn = QPushButton("Delete…")
        self.delete_btn.clicked.connect(self._on_delete_ruleset)
        top.addWidget(new_btn)
        top.addWidget(dup_btn)
        top.addWidget(self.delete_btn)
        root.addLayout(top)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("Description:"))
        self.description_edit = QLineEdit()
        desc_row.addWidget(self.description_edit, 1)
        root.addLayout(desc_row)

        self.tabs = QTabWidget()
        self.rules_edit = QPlainTextEdit()
        self.vehicles_md_edit = QPlainTextEdit()
        self.vehicles_json_edit = QPlainTextEdit()
        self.events_json_edit = QPlainTextEdit()
        self.news_json_edit = QPlainTextEdit()
        self.agencies_table = QTableWidget(0, 4)
        self.agencies_table.setHorizontalHeaderLabels(
            ["Name", "Activation year", "Description", "Excuse causes (* = all, or comma-separated)"]
        )
        self.agencies_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.agencies_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.transit_companies_edit = QPlainTextEdit()
        self.transit_companies_edit.setPlaceholderText("One transit company name per line")
        self.default_line_names_edit = QPlainTextEdit()
        self.default_line_names_edit.setPlaceholderText("Optional default line names, one per line")
        self._editor_widgets = (
            self.rules_edit,
            self.vehicles_md_edit,
            self.vehicles_json_edit,
            self.events_json_edit,
            self.news_json_edit,
            self.transit_companies_edit,
            self.default_line_names_edit,
        )
        self._agency_edit_triggers = (
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._tab_save_buttons = []
        self._regen_vehicles_btn = None
        for label, editor in (
            ("RULES.md", self.rules_edit),
            ("VEHICLES.md", self.vehicles_md_edit),
            ("vehicles.json", self.vehicles_json_edit),
            ("events.json", self.events_json_edit),
        ):
            tab = QWidget()
            lay = QVBoxLayout()
            tab.setLayout(lay)
            lay.addWidget(editor)
            save_row = QHBoxLayout()
            save_btn = QPushButton(f"Save {label}")
            save_btn.clicked.connect(lambda checked=False, lbl=label: self._save_tab(lbl))
            self._tab_save_buttons.append(save_btn)
            if label == "VEHICLES.md":
                regen_btn = QPushButton("Regenerate from vehicles.json")
                regen_btn.clicked.connect(self._regenerate_vehicles_md)
                self._regen_vehicles_btn = regen_btn
                save_row.addWidget(regen_btn)
            save_row.addWidget(save_btn)
            save_row.addStretch()
            lay.addLayout(save_row)
            self.tabs.addTab(tab, label)
        self._build_news_tab()
        self._build_organizations_tab()
        root.addWidget(self.tabs)
        if 0 <= initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(initial_tab)

        self._default_readonly_label = QLabel("")
        self._default_readonly_label.setWordWrap(True)
        self._default_readonly_label.setStyleSheet("color: #a60; font-weight: bold;")
        root.addWidget(self._default_readonly_label)

        btn_row = QHBoxLayout()
        save_all_btn = QPushButton("Save All")
        save_all_btn.clicked.connect(self._save_all)
        save_active_btn = QPushButton("Save All && Set Active")
        save_active_btn.clicked.connect(self._save_all_and_set_active)
        set_active_btn = QPushButton("Set Active (use saved files)")
        set_active_btn.clicked.connect(self._on_set_active)
        self._save_all_btn = save_all_btn
        self._save_active_btn = save_active_btn
        self._set_active_btn = set_active_btn
        btn_row.addWidget(save_all_btn)
        btn_row.addWidget(save_active_btn)
        btn_row.addWidget(set_active_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._reload_ruleset_combo()
        self._load_all_editors()
        self._update_ruleset_permissions()

    def _editor_snapshot(self) -> dict:
        return {
            "rules_md": self.rules_edit.toPlainText(),
            "vehicles_md": self.vehicles_md_edit.toPlainText(),
            "vehicles_json": self.vehicles_json_edit.toPlainText(),
            "events_json": self.events_json_edit.toPlainText(),
            "news_json": self.news_json_edit.toPlainText(),
            "organizations_json": self._organizations_json_from_ui(),
            "description": self.description_edit.text(),
        }

    def _build_news_tab(self):
        tab = QWidget()
        lay = QVBoxLayout()
        tab.setLayout(lay)
        lay.addWidget(QLabel(
            "Yearly news filler articles. JSON with economy, politics, and oddities arrays.\n"
            "Economy/politics: [\"Headline\", \"Body text\"]. Oddities: [\"Headline\", \"Body\", min_year]."
        ))
        lay.addWidget(self.news_json_edit, 1)
        save_row = QHBoxLayout()
        save_btn = QPushButton("Save news.json")
        save_btn.clicked.connect(lambda checked=False: self._save_tab("news.json"))
        self._tab_save_buttons.append(save_btn)
        save_row.addWidget(save_btn)
        save_row.addStretch()
        lay.addLayout(save_row)
        self.tabs.addTab(tab, "news.json")

    def _build_organizations_tab(self):
        tab = QWidget()
        lay = QVBoxLayout()
        tab.setLayout(lay)
        lay.addWidget(QLabel(
            "Agencies (NIMBYs, EPA, etc.) can sue players. Use * in Excuse causes for any lawsuit type, "
            "or comma-separated causes (e.g. Environmental Damage)."
        ))
        agency_btns = QHBoxLayout()
        self._add_agency_btn = QPushButton("Add agency")
        self._add_agency_btn.clicked.connect(self._add_agency_row)
        self._remove_agency_btn = QPushButton("Remove selected")
        self._remove_agency_btn.clicked.connect(self._remove_selected_agency_rows)
        agency_btns.addWidget(self._add_agency_btn)
        agency_btns.addWidget(self._remove_agency_btn)
        agency_btns.addStretch()
        lay.addLayout(agency_btns)
        lay.addWidget(self.agencies_table)
        lay.addWidget(QLabel("Transit company names (one per line):"))
        lay.addWidget(self.transit_companies_edit, 1)
        lay.addWidget(QLabel("Default line names (optional, one per line):"))
        lay.addWidget(self.default_line_names_edit, 1)
        save_row = QHBoxLayout()
        save_btn = QPushButton("Save organizations.json")
        save_btn.clicked.connect(lambda checked=False: self._save_tab("organizations.json"))
        self._tab_save_buttons.append(save_btn)
        save_row.addWidget(save_btn)
        save_row.addStretch()
        lay.addLayout(save_row)
        self.tabs.addTab(tab, "organizations.json")

    def _organizations_json_from_ui(self) -> str:
        from ruleset_catalog import format_organizations_json

        agencies = []
        for row in range(self.agencies_table.rowCount()):
            def _cell(col, default=""):
                item = self.agencies_table.item(row, col)
                return item.text().strip() if item else default

            name = _cell(0)
            if not name:
                continue
            try:
                year = int(_cell(1, "1830"))
            except ValueError:
                year = 1830
            causes_raw = _cell(3, "*")
            if causes_raw in ("*", "all"):
                excuse_causes = ["*"]
            else:
                excuse_causes = [c.strip() for c in causes_raw.split(",") if c.strip()]
            agencies.append({
                "name": name,
                "activation_year": year,
                "description": _cell(2),
                "excuse_causes": excuse_causes or ["*"],
            })
        transit = [
            ln.strip()
            for ln in self.transit_companies_edit.toPlainText().splitlines()
            if ln.strip()
        ]
        default_lines = [
            ln.strip()
            for ln in self.default_line_names_edit.toPlainText().splitlines()
            if ln.strip()
        ]
        return format_organizations_json({
            "version": 1,
            "agencies": agencies,
            "transit_companies": transit,
            "default_line_names": default_lines,
        })

    def _load_organizations_into_ui(self, org: dict):
        self.agencies_table.setRowCount(0)
        for agency in org.get("agencies") or []:
            row = self.agencies_table.rowCount()
            self.agencies_table.insertRow(row)
            causes = agency.get("excuse_causes") or ["*"]
            if "*" in causes or "all" in causes:
                causes_text = "*"
            else:
                causes_text = ", ".join(str(c) for c in causes)
            values = (
                str(agency.get("name", "")),
                str(int(agency.get("activation_year", 1830))),
                str(agency.get("description", "")),
                causes_text,
            )
            for col, text in enumerate(values):
                self.agencies_table.setItem(row, col, QTableWidgetItem(text))
        self.transit_companies_edit.setPlainText(
            "\n".join(org.get("transit_companies") or [])
        )
        self.default_line_names_edit.setPlainText(
            "\n".join(org.get("default_line_names") or [])
        )

    def _add_agency_row(self):
        row = self.agencies_table.rowCount()
        self.agencies_table.insertRow(row)
        for col, text in enumerate(("New Agency", "1900", "", "*")):
            self.agencies_table.setItem(row, col, QTableWidgetItem(text))

    def _remove_selected_agency_rows(self):
        rows = sorted({idx.row() for idx in self.agencies_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.agencies_table.removeRow(row)

    def _is_dirty(self) -> bool:
        if self._baseline is None:
            return False
        return self._editor_snapshot() != self._baseline

    def _prompt_save_if_dirty(self) -> bool:
        """Return True if OK to discard or after save; False if user cancelled."""
        if not self._is_dirty():
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to ruleset '{self._ruleset_name}' before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            if not self._save_all(silent=False):
                return False
        return True

    def _reload_ruleset_combo(self):
        from ruleset_loader import list_rulesets, get_active_ruleset
        self.ruleset_combo.blockSignals(True)
        self.ruleset_combo.clear()
        for name in list_rulesets():
            self.ruleset_combo.addItem(name)
        idx = self.ruleset_combo.findText(self._ruleset_name or get_active_ruleset())
        if idx >= 0:
            self.ruleset_combo.setCurrentIndex(idx)
        self.ruleset_combo.blockSignals(False)

    def _on_ruleset_combo_index_changed(self, index: int):
        if self._loading or index < 0:
            return
        name = self.ruleset_combo.currentText()
        if not name or name == self._ruleset_name:
            return
        if not self._prompt_save_if_dirty():
            self._reload_ruleset_combo()
            return
        self._ruleset_name = name
        self._load_all_editors()

    def _load_all_editors(self):
        from ruleset_loader import read_ruleset_bundle
        self._loading = True
        bundle = read_ruleset_bundle(self._ruleset_name)
        self.rules_edit.setPlainText(bundle["rules_md"])
        self.vehicles_md_edit.setPlainText(bundle["vehicles_md"])
        self.vehicles_json_edit.setPlainText(bundle["vehicles_json"])
        self.events_json_edit.setPlainText(bundle["events_json"])
        self.news_json_edit.setPlainText(bundle.get("news_json", "{}"))
        from ruleset_catalog import parse_organizations_json
        org = parse_organizations_json(bundle.get("organizations_json", "{}"))
        self._load_organizations_into_ui(org)
        self.description_edit.setText(bundle.get("description", ""))
        self._baseline = self._editor_snapshot()
        self._loading = False
        self._update_ruleset_permissions()

    def _update_ruleset_permissions(self):
        from ruleset_loader import is_default_ruleset

        readonly = is_default_ruleset(self._ruleset_name)
        for editor in self._editor_widgets:
            editor.setReadOnly(readonly)
        self.description_edit.setReadOnly(readonly)
        if readonly:
            self.agencies_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        else:
            self.agencies_table.setEditTriggers(self._agency_edit_triggers)
        self._add_agency_btn.setEnabled(not readonly)
        self._remove_agency_btn.setEnabled(not readonly)
        for btn in self._tab_save_buttons:
            btn.setEnabled(not readonly)
        if self._regen_vehicles_btn:
            self._regen_vehicles_btn.setEnabled(not readonly)
        self._save_all_btn.setEnabled(not readonly)
        self._save_active_btn.setEnabled(not readonly)
        self.delete_btn.setEnabled(not readonly)
        self.delete_btn.setToolTip(
            "The default ruleset cannot be deleted."
            if readonly
            else "Delete this custom ruleset from disk."
        )
        if readonly:
            self._default_readonly_label.setText(
                "The default ruleset is read-only. Use Duplicate… to create an editable copy."
            )
        else:
            self._default_readonly_label.setText("")

    def _save_all(self, silent: bool = False) -> bool:
        from ruleset_loader import is_default_ruleset, save_ruleset_bundle, read_vehicles_markdown
        if is_default_ruleset(self._ruleset_name):
            QMessageBox.information(
                self,
                "Default ruleset",
                "The default ruleset cannot be edited. Duplicate it to create a custom ruleset.",
            )
            return False
        snap = self._editor_snapshot()
        try:
            save_ruleset_bundle(
                self._ruleset_name,
                rules_md=snap["rules_md"],
                vehicles_md=snap["vehicles_md"],
                vehicles_json=snap["vehicles_json"],
                events_json=snap["events_json"],
                news_json=snap["news_json"],
                organizations_json=snap["organizations_json"],
                description=snap["description"],
            )
            self.vehicles_md_edit.setPlainText(read_vehicles_markdown(self._ruleset_name))
            self._baseline = self._editor_snapshot()
            if not silent:
                QMessageBox.information(
                    self, "Saved",
                    f"All files saved for ruleset '{self._ruleset_name}'.",
                )
            return True
        except Exception as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return False

    def _save_tab(self, label: str):
        if self._save_all(silent=True):
            QMessageBox.information(self, "Saved", f"{label} saved for ruleset '{self._ruleset_name}'.")

    def _regenerate_vehicles_md(self):
        from ruleset_loader import regenerate_vehicles_md, read_vehicles_markdown
        try:
            regenerate_vehicles_md(self._ruleset_name)
            self.vehicles_md_edit.setPlainText(read_vehicles_markdown(self._ruleset_name))
            self._baseline = self._editor_snapshot()
            QMessageBox.information(self, "Regenerated", "VEHICLES.md updated from vehicle registry.")
        except Exception as e:
            QMessageBox.warning(self, "Regenerate failed", str(e))

    def _save_all_and_set_active(self):
        if self._save_all(silent=True):
            self._on_set_active()

    def _on_new_ruleset(self):
        if not self._prompt_save_if_dirty():
            return
        from ruleset_loader import create_ruleset
        name, ok = QInputDialog.getText(self, "New Ruleset", "Ruleset name:")
        if not ok or not name.strip():
            return
        try:
            safe = create_ruleset(name.strip(), copy_from=self._ruleset_name, set_active=False)
            self._ruleset_name = safe
            self._reload_ruleset_combo()
            self._load_all_editors()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_duplicate_ruleset(self):
        if not self._prompt_save_if_dirty():
            return
        from ruleset_loader import create_ruleset
        name, ok = QInputDialog.getText(self, "Duplicate Ruleset", "New ruleset name:")
        if not ok or not name.strip():
            return
        try:
            if self._is_dirty():
                self._save_all(silent=True)
            safe = create_ruleset(name.strip(), copy_from=self._ruleset_name, set_active=False)
            self._ruleset_name = safe
            self._reload_ruleset_combo()
            self._load_all_editors()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_delete_ruleset(self):
        from ruleset_loader import delete_ruleset, is_default_ruleset, get_active_ruleset
        if is_default_ruleset(self._ruleset_name):
            QMessageBox.information(
                self, "Default ruleset",
                "The default ruleset cannot be deleted. Duplicate it to create a custom ruleset.",
            )
            return
        reply = QMessageBox.question(
            self, "Delete ruleset",
            f"Delete ruleset '{self._ruleset_name}' permanently?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            name = self._ruleset_name
            delete_ruleset(name)
            self._ruleset_name = get_active_ruleset()
            self._reload_ruleset_combo()
            self._load_all_editors()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_set_active(self):
        from ruleset_loader import set_active_ruleset, activate_vehicle_registry_for_ruleset
        try:
            set_active_ruleset(self._ruleset_name)
            activate_vehicle_registry_for_ruleset(self._ruleset_name)
            QMessageBox.information(
                self, "Active ruleset",
                f"'{self._ruleset_name}' is now active (new games use its saved files).",
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_close(self):
        if self._prompt_save_if_dirty():
            self.accept()

    def reject(self):
        self._on_close()


class GameModeDialog(QDialog):
    """Dialog to choose between local and networked gameplay"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        from ruleset_loader import ensure_rulesets_layout, get_active_ruleset
        ensure_rulesets_layout()
        self.setWindowTitle("Railroad Baron - Choose Game Mode")
        apply_application_logo(self)
        self.setGeometry(300, 300, 520, 640)
        self.setModal(True)
        
        layout = QVBoxLayout()
        self.setLayout(layout)

        splash_path = APP_SPLASH_PATH if APP_SPLASH_PATH.is_file() else APP_LOGO_PATH
        if splash_path.is_file():
            splash_label = QLabel()
            pixmap = QPixmap(str(splash_path))
            if not pixmap.isNull():
                splash_label.setPixmap(pixmap.scaled(
                    480, 280,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                splash_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(splash_label)
        
        title = QLabel("Welcome to Railroad Baron!")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        active_rs = get_active_ruleset()
        self.ruleset_label = QLabel(f"Ruleset: {active_rs} (change in Game settings before starting)")
        self.ruleset_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ruleset_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.ruleset_label)
        
        instructions = QLabel("Choose how you want to play:")
        layout.addWidget(instructions)
        
        button_layout = QVBoxLayout()
        
        self.local_button = QPushButton("Play on PC (Local)")
        self.local_button.setMinimumHeight(50)
        self.local_button.clicked.connect(self.accept_local)
        button_layout.addWidget(self.local_button)
        
        self.network_button = QPushButton("Play Networked (Multiplayer)")
        self.network_button.setMinimumHeight(50)
        self.network_button.clicked.connect(self.accept_network)
        button_layout.addWidget(self.network_button)

        self.rulesets_button = QPushButton("Manage Rulesets…")
        self.rulesets_button.setMinimumHeight(44)
        self.rulesets_button.clicked.connect(self._open_ruleset_editor)
        button_layout.addWidget(self.rulesets_button)
        
        layout.addLayout(button_layout)
        
        self.choice = None  # 'local' or 'network'

    def _open_ruleset_editor(self):
        dlg = RulesetEditorDialog(self)
        if dlg.exec():
            from ruleset_loader import get_active_ruleset
            self.ruleset_label.setText(
                f"Ruleset: {get_active_ruleset()} (change in Game settings before starting)"
            )
    
    def accept_local(self):
        self.choice = 'local'
        self.accept()
    
    def accept_network(self):
        if NETWORKING_AVAILABLE:
            self.choice = 'network'
            self.accept()
        else:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(self, "Networking Libraries Required", 
                              "Networking libraries (aiohttp and python-socketio) are not installed.\n\n"
                              "Would you like to continue anyway? (You'll need to install the libraries to use multiplayer.)",
                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.choice = 'network'
                self.accept()


class NetworkedMainWindow(MainWindow):
    """MainWindow with networking support"""
    
    def __init__(self, network_mode=False):
        # Set network mode BEFORE calling super().__init__() so it's available during initialization
        self.is_network_mode = network_mode
        self.network_client = None
        self.network_thread = None
        self.network_session_id = None
        self._local_server_process = None
        
        # Initialize parent class
        super().__init__()
        
        # If network mode, add network settings tab
        if network_mode:
            self.setup_network_tabs()
    
    def setup_network_tabs(self):
        """Setup network settings tab and players tab alongside game tab"""
        # The game tab is already created by parent __init__
        # We need to add a Players tab and Network Settings tab
        
        # Create Players tab
        self.players_tab = QWidget()
        self.players_layout = QVBoxLayout()
        self.players_tab.setLayout(self.players_layout)
        
        # Insert it as the second tab (index 1)
        self.tabs.insertTab(1, self.players_tab, "Players")
        
        # Add players UI
        self.setup_players_ui()
        
        # Create Network Settings tab
        self.network_tab = QWidget()
        self.network_layout = QVBoxLayout()
        self.network_tab.setLayout(self.network_layout)
        
        # Insert it as the third tab (index 2)
        self.tabs.insertTab(2, self.network_tab, "Network Settings")
        
        # Add network settings UI
        self.setup_network_settings_ui()
    
    def setup_players_ui(self):
        """Setup the players tab UI"""
        # Title
        title = QLabel("Connected Players")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.players_layout.addWidget(title)
        
        self.players_layout.addWidget(QLabel(""))  # Spacing
        
        # Players list
        self.players_list_label = QLabel("No players connected to server")
        self.players_list_label.setWordWrap(True)
        self.players_layout.addWidget(self.players_list_label)
        
        # Refresh button
        refresh_button = QPushButton("Refresh Players List")
        refresh_button.clicked.connect(self.refresh_players_list)
        self.players_layout.addWidget(refresh_button)
        
        self.players_layout.addStretch()  # Push everything to top
    
    def refresh_players_list(self):
        """Refresh the list of connected players"""
        self.update_players_tab()
    
    def update_players_tab(self):
        """Update the Players tab with current connected players"""
        if not hasattr(self, 'players_list_label'):
            return
        
        if not self.is_network_mode or not self.network_client or not self.network_client.connected_flag:
            self.players_list_label.setText("Not connected to server. Connect first in the Network Settings tab.")
            return
        
        # Show players from current game session
        if hasattr(self, 'ui') and self.operations:
            players = self.operations.get_players()
            if players:
                player_names = [self.operations.get_player_name(p) for p in players]
                player_list_text = "Players connected to server:\n\n"
                for i, name in enumerate(player_names, 1):
                    # Highlight current player
                    if i - 1 == self.current_player_index:
                        player_list_text += f"{i}. {name} (You)\n"
                    else:
                        player_list_text += f"{i}. {name}\n"
                self.players_list_label.setText(player_list_text)
            else:
                self.players_list_label.setText("No players in current game session.")
        else:
            self.players_list_label.setText("No game session active. Create or join a game first.")
    
    def setup_network_settings_ui(self):
        """Setup the network settings tab UI"""
        # Title
        title = QLabel("Network Settings")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.network_layout.addWidget(title)
        
        self.network_layout.addWidget(QLabel(""))  # Spacing
        
        # Server host (IP or hostname) and port
        host_layout = QHBoxLayout()
        host_layout.addWidget(QLabel("Server IP/Host:"))
        self.server_host_input = QLineEdit("localhost")
        self.server_host_input.setPlaceholderText("e.g. 192.168.1.5 or localhost")
        host_layout.addWidget(self.server_host_input)
        self.network_layout.addLayout(host_layout)

        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("Port:"))
        from PyQt6.QtWidgets import QSpinBox
        self.server_port_spin = QSpinBox()
        self.server_port_spin.setRange(1, 65535)
        self.server_port_spin.setValue(8080)
        port_layout.addWidget(self.server_port_spin)
        port_layout.addStretch()
        self.network_layout.addLayout(port_layout)

        # Legacy alias for tests / older code paths
        self.server_url_input = self.server_host_input
        
        self.network_layout.addWidget(QLabel(""))  # Spacing
        
        # Connection status
        self.network_status_label = QLabel("Status: Not connected")
        self.network_status_label.setStyleSheet("font-weight: bold;")
        self.network_layout.addWidget(self.network_status_label)
        
        self.network_layout.addWidget(QLabel(""))  # Spacing
        
        # Connect/Disconnect button
        button_layout = QHBoxLayout()
        self.connect_button = QPushButton("Connect to Server")
        self.connect_button.clicked.connect(self.connect_to_server)
        button_layout.addWidget(self.connect_button)
        
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.disconnect_from_server)
        self.disconnect_button.setEnabled(False)
        button_layout.addWidget(self.disconnect_button)
        
        self.network_layout.addLayout(button_layout)
        
        self.network_layout.addWidget(QLabel(""))  # Spacing

        # Optional local server hosting
        host_server_layout = QHBoxLayout()
        self.start_server_button = QPushButton("Start Local Server")
        self.start_server_button.clicked.connect(self._start_local_server)
        host_server_layout.addWidget(self.start_server_button)
        self.stop_server_button = QPushButton("Stop Local Server")
        self.stop_server_button.clicked.connect(self._stop_local_server)
        self.stop_server_button.setEnabled(False)
        host_server_layout.addWidget(self.stop_server_button)
        self.network_layout.addLayout(host_server_layout)
        
        self.network_layout.addWidget(QLabel(""))  # Spacing
        
        # Session info
        self.session_info_label = QLabel("Session ID: None")
        self.network_layout.addWidget(self.session_info_label)
        
        self.network_layout.addWidget(QLabel(""))  # Spacing
        
        # Join game section
        join_layout = QVBoxLayout()
        join_layout.addWidget(QLabel("Join Existing Game:"))
        
        join_input_layout = QHBoxLayout()
        self.join_session_input = QLineEdit()
        self.join_session_input.setPlaceholderText("Enter session ID")
        join_input_layout.addWidget(self.join_session_input)
        
        self.join_button = QPushButton("Join Game")
        self.join_button.clicked.connect(self.join_existing_game)
        self.join_button.setEnabled(True)  # Enabled by default
        join_input_layout.addWidget(self.join_button)
        join_layout.addLayout(join_input_layout)
        
        self.network_layout.addLayout(join_layout)
        
        self.network_layout.addStretch()  # Push everything to top
    
    def _get_server_url_from_inputs(self) -> str:
        """Build normalized server URL from host and port inputs."""
        host = self.server_host_input.text().strip()
        port = self.server_port_spin.value()
        return parse_server_address(host, port=port)

    def _start_local_server(self):
        """Start server.py as a subprocess on the configured port."""
        if not NETWORKING_AVAILABLE:
            QMessageBox.warning(self, "Networking Not Available",
                              "Networking libraries not installed.")
            return
        if getattr(self, '_local_server_process', None) and self._local_server_process.poll() is None:
            QMessageBox.information(self, "Server Running", "Local server is already running.")
            return
        import subprocess
        port = self.server_port_spin.value()
        server_script = Path(__file__).resolve().parent / "server.py"
        try:
            self._local_server_process = subprocess.Popen(
                [sys.executable, str(server_script), str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            QMessageBox.warning(self, "Server Error", f"Failed to start local server: {e}")
            return
        self.start_server_button.setEnabled(False)
        self.stop_server_button.setEnabled(True)
        if hasattr(self, 'network_status_label'):
            self.network_status_label.setText(f"Status: Local server started on port {port}")

    def _stop_local_server(self):
        """Stop locally hosted server subprocess."""
        server_proc = getattr(self, '_local_server_process', None)
        if server_proc is None or server_proc.poll() is not None:
            self._local_server_process = None
            self.start_server_button.setEnabled(True)
            self.stop_server_button.setEnabled(False)
            return
        try:
            server_proc.terminate()
            server_proc.wait(timeout=3)
        except Exception:
            try:
                server_proc.kill()
            except Exception:
                pass
        self._local_server_process = None
        self.start_server_button.setEnabled(True)
        self.stop_server_button.setEnabled(False)
        if hasattr(self, 'network_status_label') and not getattr(self, 'network_client', None):
            self.network_status_label.setText("Status: Local server stopped")
    
    def connect_to_server(self):
        """Connect to the game server"""
        if not NETWORKING_AVAILABLE:
            QMessageBox.warning(self, "Networking Not Available", 
                              "Networking libraries not installed. Install aiohttp and python-socketio.")
            return
        
        server_url = self._get_server_url_from_inputs()
        
        try:
            # Create network client
            self.network_client = NetworkClient(server_url)
            self.is_network_mode = True
            
            # Connect signals
            self.network_client.connected.connect(self.on_network_connected)
            self.network_client.disconnected.connect(self.on_network_disconnected)
            self.network_client.game_created.connect(self.on_network_game_created)
            self.network_client.game_state_received.connect(self.on_network_game_state_received)
            self.network_client.error_received.connect(self.on_network_error)
            self.network_client.player_joined.connect(self.on_network_player_joined)
            
            # Start network thread
            self.network_thread = NetworkThread(self.network_client)
            self.network_thread.start()
            
            # Update UI
            self.network_status_label.setText(f"Status: Connecting to {server_url}...")
            self.connect_button.setEnabled(False)
            self.server_host_input.setEnabled(False)
            self.server_port_spin.setEnabled(False)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Server Address", str(e))
            self.network_status_label.setText("Status: Invalid address")
            self.connect_button.setEnabled(True)
        except Exception as e:
            QMessageBox.warning(self, "Connection Error", f"Failed to connect to server: {str(e)}\n\nMake sure the server is running.")
            self.network_status_label.setText("Status: Connection failed")
            self.connect_button.setEnabled(True)
            self.server_host_input.setEnabled(True)
            self.server_port_spin.setEnabled(True)
    
    def disconnect_from_server(self):
        """Disconnect from the game server"""
        if self.network_client:
            self.network_client.disconnect_from_server()
        if self.network_thread is not None:
            try:
                if self.network_thread.isRunning():
                    self.network_thread.wait(2000)
            except Exception:
                pass
            self.network_thread = None
        self.network_status_label.setText("Status: Disconnected")
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self.server_host_input.setEnabled(True)
        self.server_port_spin.setEnabled(True)
        self.session_info_label.setText("Session ID: None")
        self.network_session_id = None
    
    def join_existing_game(self):
        """Join an existing game session"""
        session_id = self.join_session_input.text().strip()
        if not session_id:
            QMessageBox.warning(self, "Invalid Session ID", "Please enter a session ID")
            return
        
        # If not connected, connect first
        if not self.network_client or not self.network_client.connected_flag:
            try:
                server_url = self._get_server_url_from_inputs()
            except ValueError as e:
                QMessageBox.warning(self, "Invalid Server Address", str(e))
                return
            
            # Create and connect client
            self.network_client = NetworkClient(server_url)
            self.is_network_mode = True
            
            # Connect signals
            self.network_client.connected.connect(self.on_network_connected)
            self.network_client.disconnected.connect(self.on_network_disconnected)
            self.network_client.game_created.connect(self.on_network_game_created)
            self.network_client.game_state_received.connect(self.on_network_game_state_received)
            self.network_client.error_received.connect(self.on_network_error)
            self.network_client.player_joined.connect(self.on_network_player_joined)
            self.network_client.settings_updated.connect(self.on_settings_updated)
            
            # Setup setting synchronization for game tab
            self.setup_settings_sync()
            
            # Start network thread
            self.network_thread = NetworkThread(self.network_client)
            self.network_thread.start()
            
            # Wait a moment for connection, then join
            def join_after_connect():
                if self.network_client and self.network_client.connected_flag:
                    self.network_client.join_game(session_id)
                    if hasattr(self, 'network_status_label'):
                        self.network_status_label.setText(f"Status: Joining game {session_id[:8]}...")
                else:
                    QMessageBox.warning(self, "Connection Failed", "Could not connect to server. Please check the server URL.")
            QTimer.singleShot(1000, join_after_connect)  # Wait 1 second for connection
        else:
            # Already connected, just join
            self.network_client.join_game(session_id)
            if hasattr(self, 'network_status_label'):
                self.network_status_label.setText(f"Status: Joining game {session_id[:8]}...")
    
    def on_start_game_clicked(self):
        """Start game - if networked, request server to start; otherwise start locally"""
        if self.is_network_mode and self.network_client and self.network_client.connected_flag:
            # Send start_game with AI indices so server runs AI turns each year
            total = self.player_count_spin.value() if hasattr(self, 'player_count_spin') else 4
            human_count = min(self.human_count_spin.value() if hasattr(self, 'human_count_spin') else total, total)
            ai_player_indices = list(range(human_count, total)) if total > human_count else []
            self.network_client.start_game_on_server(ai_player_indices=ai_player_indices)
        else:
            # Start game locally
            super().on_start_game_clicked()
    
    def start_network_game(self, player_names: list[str], map_width: int = 100, map_height: int = 100, 
                          starting_money: int = 20000, worker_replenishment_rate: float = 1.0):
        """Start game in network mode"""
        if not self.network_client or not self.network_client.connected_flag:
            QMessageBox.warning(self, "Not Connected", "Please connect to server first in the Network Settings tab.")
            return
        
        # Create game on server
        self.network_client.create_game(
            player_names=player_names,
            map_width=map_width,
            map_height=map_height,
            starting_money=starting_money,
            worker_replenishment_rate=worker_replenishment_rate
        )
    
    def _ensure_correct_player(self):
        """Ensure current_player_index is set to assigned player in networked mode"""
        if hasattr(self, 'assigned_player_id') and hasattr(self, 'is_network_mode') and self.is_network_mode:
            self.current_player_index = self.assigned_player_id
    
    def on_create_line_clicked(self):
        """Override to add network support with error handling"""
        try:
            self._ensure_correct_player()
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                QMessageBox.warning(self, "No Active Players", "No active players remaining.")
                return
            
            # Get line name
            from PyQt6.QtWidgets import QInputDialog
            line_name, ok = QInputDialog.getText(self, "Create Line", "Enter line name:")
            if not ok or not line_name:
                return
            
            # Create a dialog for route points
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QTextEdit
            points_dialog = QDialog(self)
            points_dialog.setWindowTitle(f"Define Route for {line_name}")
            points_dialog.setGeometry(200, 200, 500, 400)
            layout = QVBoxLayout()
            
            layout.addWidget(QLabel("Enter route points (x, y) - one per line."))
            layout.addWidget(QLabel("Example: 0,0 then 1,1 then 2,2"))
            
            points_text = QTextEdit()
            points_text.setPlaceholderText("0,0\n1,1\n2,1")
            points_text.setMinimumHeight(200)
            layout.addWidget(points_text)
            
            from PyQt6.QtWidgets import QHBoxLayout
            button_h_layout = QHBoxLayout()
            ok_btn = QPushButton("Create Line")
            cancel_btn = QPushButton("Cancel")
            button_h_layout.addWidget(ok_btn)
            button_h_layout.addWidget(cancel_btn)
            layout.addLayout(button_h_layout)
            
            points_dialog.setLayout(layout)
            
            def on_create_line():
                try:
                    # Parse points from text
                    points_text_content = points_text.toPlainText().strip()
                    if not points_text_content:
                        QMessageBox.warning(self, "Error", "Please enter at least one point")
                        return
                    
                    points = []
                    try:
                        for line in points_text_content.split('\n'):
                            if line.strip():
                                x, y = map(int, line.strip().split(','))
                                points.append((x, y))
                    except ValueError:
                        QMessageBox.warning(self, "Error", "Invalid point format. Use 'x,y' format.")
                        return
                    
                    if len(points) < 2:
                        QMessageBox.warning(self, "Error", "A line must have at least 2 points")
                        return

                    if self.is_network_mode and self.network_client:
                        construction_workers = 0
                        if hasattr(current_player, 'get_construction_worker_count'):
                            construction_workers = current_player.get_construction_worker_count()
                        
                        try:
                            self.network_client.build_line(
                                name=line_name,
                                points=points,
                                construction_workers=construction_workers
                            )
                            points_dialog.close()
                            QMessageBox.information(self, "Line Construction Queued", 
                                f"Line '{line_name}' construction request sent to server. Waiting for confirmation...")
                        except Exception as e:
                            QMessageBox.warning(self, "Network Error", f"Failed to send line creation request: {str(e)}")
                        return

                    # Local mode - get cost info from ui.py
                    cost_info = self.operations.calculate_line_cost(points)

                    reply = QMessageBox.question(self, "Confirm Line Creation",
                                                 f"Creating this line will cost ${cost_info['cost']} (distance: {cost_info['length']:.2f} units). Proceed?",
                                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if reply != QMessageBox.StandardButton.Yes:
                        return

                    result = self.operations.create_line_complete(line_name, points, current_player)
                    if result and result.get('success') is False:
                        QMessageBox.warning(self, "Cannot Create Line", result.get('error_message', 'Unknown error'))
                        return
                    self.check_and_eliminate_player(current_player)
                    QMessageBox.information(self, "Line Construction Started", 
                        f"Line '{line_name}' construction started with {result['points_count']} points!\n"
                        f"Cost: ${result['cost']}\nBuild Time: {result['build_time']} turns\nRemaining Money: ${result['remaining_money']}")
                    points_dialog.close()
                    self.refresh_construction_tab()
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to create line: {str(e)}")
                    import traceback
                    traceback.print_exc()
            
            ok_btn.clicked.connect(on_create_line)
            cancel_btn.clicked.connect(points_dialog.close)
            
            points_dialog.exec()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open line creation dialog: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def on_create_station_clicked(self):
        """Override to add network support with error handling"""
        try:
            self._ensure_correct_player()
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                QMessageBox.warning(self, "No Active Players", "No active players remaining.")
                return
            
            if self.is_network_mode and self.network_client:
                QMessageBox.information(self, "Network Mode", 
                    "Station creation is not yet implemented in network mode. "
                    "This feature will be available in a future update.")
                return
            
            super().on_create_station_clicked()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to create station: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def on_assign_trainset_clicked(self):
        """Override to add network support with error handling"""
        try:
            self._ensure_correct_player()
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                QMessageBox.warning(self, "No Active Players", "No active players remaining.")
                return
            
            if self.is_network_mode and self.network_client:
                # Get trainset and line info from dialog first
                unassigned = self.operations.get_unassigned_trainsets(current_player)
                if not unassigned:
                    QMessageBox.information(self, "Assign Trainset", "No unassigned trainsets available.")
                    return
                
                # Use dialog to get selection, then send to server
                from PyQt6.QtWidgets import QDialog, QVBoxLayout, QComboBox, QPushButton, QLabel
                dlg = QDialog(self)
                dlg.setWindowTitle("Assign Trainset to Line")
                dlg.setGeometry(200, 200, 400, 200)
                l = QVBoxLayout()
                
                l.addWidget(QLabel("Select Trainset:"))
                ts_combo = QComboBox()
                ts_options = []
                for i, ts in enumerate(unassigned):
                    loco_name = self.operations.get_trainset_locomotive_name(ts)
                    opt = f"Trainset {i}: {loco_name}"
                    ts_options.append(opt)
                ts_combo.addItems(ts_options)
                l.addWidget(ts_combo)
                
                line_names = self.operations.get_player_line_names(current_player)
                if not line_names:
                    QMessageBox.information(self, "Assign Trainset", "Player has no lines.")
                    return
                
                l.addWidget(QLabel("Select Line:"))
                line_combo = QComboBox()
                line_combo.addItems(line_names)
                l.addWidget(line_combo)
                
                def on_assign():
                    trainset_index = ts_combo.currentIndex()
                    line_name = line_combo.currentText()
                    if self.network_client:
                        self.network_client.assign_trainset(trainset_index, line_name)
                        QMessageBox.information(self, "Assignment Requested", 
                            f"Assigning trainset to {line_name}. Waiting for server confirmation...")
                        dlg.close()
                
                ok_btn = QPushButton("Assign")
                ok_btn.clicked.connect(on_assign)
                cancel_btn = QPushButton("Cancel")
                cancel_btn.clicked.connect(dlg.close)
                l.addWidget(ok_btn)
                l.addWidget(cancel_btn)
                dlg.setLayout(l)
                dlg.exec()
                return
            
            super().on_assign_trainset_clicked()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to assign trainset: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def on_upgrade_line_clicked(self):
        """Override to add network support with error handling"""
        try:
            if self.is_network_mode and self.network_client:
                QMessageBox.information(self, "Network Mode", 
                    "Line upgrades are not yet implemented in network mode.")
                return
            super().on_upgrade_line_clicked()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to upgrade line: {str(e)}")

    def on_upgrade_line_for_line(self, line, player):
        """Override to add network support"""
        try:
            if self.is_network_mode and self.network_client:
                QMessageBox.information(self, "Network Mode", 
                    "Line upgrades are not yet implemented in network mode.")
                return
            super().on_upgrade_line_for_line(line, player)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to upgrade line: {str(e)}")
    
    def on_extend_line_clicked(self):
        """Override to add network support with error handling"""
        try:
            if self.is_network_mode and self.network_client:
                QMessageBox.information(self, "Network Mode", 
                    "Line extensions are not yet implemented in network mode.")
                return
            super().on_extend_line_clicked()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to extend line: {str(e)}")
    
    def on_rent_workers_clicked(self, worker_type, quantity: int, current_year: int):
        """Override to add network support with error handling"""
        try:
            if self.is_network_mode and self.network_client:
                # Send rent workers action to server
                self._ensure_correct_player()
                worker_type_name = worker_type.name if hasattr(worker_type, 'name') else str(worker_type)
                self.network_client.rent_workers(worker_type_name, quantity)
                QMessageBox.information(self, "Rent Workers Requested", 
                    f"Requesting to rent {quantity} {worker_type_name}. Waiting for server confirmation...")
                return
            super().on_rent_workers_clicked(worker_type, quantity, current_year)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to rent workers: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def on_assign_workers(self, build_index: int, num_workers: int):
        """Override to add network support with error handling"""
        try:
            if self.is_network_mode and self.network_client:
                QMessageBox.information(self, "Network Mode", 
                    "Worker assignment is not yet implemented in network mode.")
                return
            super().on_assign_workers(build_index, num_workers)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to assign workers: {str(e)}")
    
    def on_player_info_button_clicked(self):
        """Override to ensure correct player context in networked mode"""
        try:
            self._ensure_correct_player()
            
            # Check if game has started
            if not hasattr(self, 'ui') or self.operations is None:
                QMessageBox.information(self, "Player Info", "Game has not started yet.")
                return
            
            # Get the correct player for networked mode
            try:
                current_player = self.operations.get_current_player(self.current_player_index)
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Error", f"Failed to get current player: {str(e)}")
                import traceback
                traceback.print_exc()
                return
            if not current_player:
                QMessageBox.information(self, "Player Info", "No active players remaining.")
                return
            
            # Build player info safely with extensive error handling
            out = []
            try:
                # Get player name and money with error handling
                try:
                    player_name = self.operations.get_player_name(current_player)
                except:
                    player_name = "Unknown Player"
                
                try:
                    player_money = self.operations.get_player_money(current_player)
                except:
                    player_money = 0
                
                header = f"{player_name}: Money: ${player_money}"
                out.append(header)
                
                # Get current year for calculations
                current_year = self.operations.get_year()
                
                # Lines owned
                try:
                    line_names = self.operations.get_player_line_names(current_player)
                    if line_names:
                        out.append("  Lines:")
                        for lname in line_names:
                            try:
                                line = self.operations.get_map_lines().get(lname)
                                if line:
                                    try:
                                        length = len(self.operations.get_line_route(line))
                                        profit = self.operations.calc_line_profit(line, current_year)
                                        mait = self.operations.calc_line_maintenance(line, current_year)
                                        out.append(f"    - {lname}: points={length}, profit=${int(profit)}, maintenance=${int(mait)}")
                                    except Exception as e:
                                        out.append(f"    - {lname} (error getting details: {str(e)})")
                                else:
                                    out.append(f"    - {lname}")
                            except Exception as e:
                                out.append(f"    - {lname} (error: {str(e)})")
                    else:
                        out.append("  Lines: none")
                except Exception as e:
                    out.append(f"  Lines: error getting lines - {str(e)}")
                
                # Trainsets owned
                try:
                    trainsets = self.operations.get_player_trainsets(current_player)
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
                                try:
                                    cap = self.operations.get_trainset_capacity(ts)
                                except:
                                    pass
                                mait = 0
                                try:
                                    mait = self.operations.get_trainset_maintenance_costs(ts)
                                except:
                                    pass
                                assign = None
                                try:
                                    assign = self.operations.get_trainset_line_assignment(ts)
                                except:
                                    pass
                                out.append(f"    - Trainset {i}: loco={loco_name}, cars={cars}, cap={cap}, maintenance=${mait}, assigned_to={assign}")
                            except Exception as e:
                                out.append(f"    - Trainset {i}: error - {str(e)}")
                    else:
                        out.append("  Trainsets: none")
                except Exception as e:
                    out.append(f"  Trainsets: error getting trainsets - {str(e)}")
                
                # Financial summary
                try:
                    player_profit = self.operations.calc_player_profit(current_player)
                except:
                    player_profit = 0
                
                try:
                    trainset_maintenance = 0
                    for ts in self.operations.get_player_trainsets(current_player):
                        try:
                            maint_cost = self.operations.get_trainset_maintenance_costs(ts)
                            if maint_cost is not None:
                                trainset_maintenance += maint_cost
                        except:
                            pass
                except:
                    trainset_maintenance = 0
                
                try:
                    line_maintenance = self.operations.calc_player_maintenance(current_player)
                except:
                    line_maintenance = 0
                try:
                    BASE_POLL_TAX = 100
                    BASE_PROPERTY_TAX_PER_LINE = 100
                    BASE_PROPERTY_TAX_PER_TRAINSET = 50
                    inflation_factor = get_inflation_factor(current_year, max_factor=10.0)
                    POLL_TAX = int(BASE_POLL_TAX * inflation_factor)
                    PROPERTY_TAX_PER_LINE = int(BASE_PROPERTY_TAX_PER_LINE * inflation_factor)
                    PROPERTY_TAX_PER_TRAINSET = int(BASE_PROPERTY_TAX_PER_TRAINSET * inflation_factor)
                    num_lines = len(self.operations.get_player_line_names(current_player)) if self.operations.get_player_line_names(current_player) else 0
                    num_trainsets = len(self.operations.get_player_trainsets(current_player)) if self.operations.get_player_trainsets(current_player) else 0
                    poll_tax = POLL_TAX
                    property_tax = (PROPERTY_TAX_PER_LINE * num_lines) + (PROPERTY_TAX_PER_TRAINSET * num_trainsets)
                except:
                    poll_tax = 100
                    property_tax = 0
                
                total_costs = int(trainset_maintenance + line_maintenance + poll_tax + property_tax)
                net = int(player_profit) - total_costs
                
                out.append("  Financial Summary:")
                out.append(f"    - Profit (lines): ${int(player_profit)}")
                out.append(f"    - Trainset maintenance: ${int(trainset_maintenance)}")
                out.append(f"    - Line maintenance: ${int(line_maintenance)}")
                out.append(f"    - Taxes: poll=${poll_tax}, property=${property_tax}")
                out.append(f"    - Net this year: ${net}")
                
            except Exception as e:
                out.append(f"Error building player info: {str(e)}")
                import traceback
                traceback.print_exc()
            
            # Display the info safely
            try:
                from PyQt6.QtWidgets import QMessageBox
                info_text = "\n".join(out) if out else "No player information available."
                # Limit message length to avoid issues
                if len(info_text) > 10000:
                    info_text = info_text[:10000] + "\n... (truncated)"
                QMessageBox.information(self, "Player Info", info_text)
            except Exception as e:
                # If QMessageBox fails, try alternative display
                try:
                    logger.debug("Player Info (could not display dialog):")
                    logger.debug("\n".join(out))
                except:
                    pass
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Error", f"Failed to display player info: {str(e)}")
            
        except Exception as e:
            try:
                from PyQt6.QtWidgets import QMessageBox
                error_msg = f"Failed to get player info: {str(e)}"
                import traceback
                tb = traceback.format_exc()
                logger.error("Get Player Info Error: %s", error_msg)
                logger.debug(tb)
                QMessageBox.warning(self, "Error", error_msg)
            except Exception as e2:
                logger.error("Critical error in error handling: %s", str(e2))
                import traceback
                traceback.print_exc()
    
    def on_sell_trainset_clicked(self):
        """Override to add network support with error handling"""
        try:
            self._ensure_correct_player()
            current_player = self.operations.get_current_player(self.current_player_index)
            if not current_player:
                QMessageBox.warning(self, "No Active Players", "No active players remaining.")
                return
            
            if self.is_network_mode and self.network_client:
                # Get player's trainsets
                trainsets = self.operations.get_player_trainsets(current_player)
                if not trainsets:
                    QMessageBox.information(self, "No Trainsets", "You don't have any trainsets to sell.")
                    return
                
                # Use dialog to get selection, then send to server
                from PyQt6.QtWidgets import QDialog, QVBoxLayout, QComboBox, QPushButton, QLabel
                dlg = QDialog(self)
                dlg.setWindowTitle("Sell Trainset for Half Price")
                dlg.setGeometry(200, 200, 500, 300)
                l = QVBoxLayout()
                
                l.addWidget(QLabel("Select trainset to sell (you will receive half its original purchase price):"))
                ts_combo = QComboBox()
                trainset_info = []
                for i, trainset in enumerate(trainsets):
                    loco_name = self.operations.get_trainset_locomotive_name(trainset)
                    num_cars = len(self.operations.get_trainset_cars(trainset))
                    capacity = self.operations.get_trainset_capacity(trainset)
                    current_year = self.operations.get_year()
                    original_price = trainset.get_price(current_year)
                    sale_price = original_price // 2
                    desc = f"Trainset {i}: {loco_name} ({num_cars} cars) - ${sale_price} (half of ${original_price})"
                    ts_combo.addItem(desc, trainset)
                    trainset_info.append({'sale_price': sale_price, 'original_price': original_price})
                l.addWidget(ts_combo)
                
                price_label = QLabel()
                if trainset_info:
                    price_label.setText(f"Sale Price: ${trainset_info[0]['sale_price']} (half of ${trainset_info[0]['original_price']})")
                def update_price(index):
                    if 0 <= index < len(trainset_info):
                        info = trainset_info[index]
                        price_label.setText(f"Sale Price: ${info['sale_price']} (half of ${info['original_price']})")
                ts_combo.currentIndexChanged.connect(update_price)
                l.addWidget(price_label)
                
                def on_sell():
                    trainset_index = ts_combo.currentIndex()
                    if self.network_client:
                        self.network_client.sell_trainset(trainset_index)
                        QMessageBox.information(self, "Sell Requested", 
                            f"Selling trainset for ${trainset_info[trainset_index]['sale_price']}. Waiting for server confirmation...")
                        dlg.close()
                
                ok_btn = QPushButton("Sell for Half Price")
                ok_btn.clicked.connect(on_sell)
                cancel_btn = QPushButton("Cancel")
                cancel_btn.clicked.connect(dlg.close)
                l.addWidget(ok_btn)
                l.addWidget(cancel_btn)
                dlg.setLayout(l)
                dlg.exec()
                return
            
            super().on_sell_trainset_clicked()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to sell trainset: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def on_network_connected(self, data):
        """Handle network connection"""
        if hasattr(self, 'network_status_label'):
            self.network_status_label.setText("Status: Connected")
        if hasattr(self, 'disconnect_button'):
            self.disconnect_button.setEnabled(True)
    
    def on_network_disconnected(self):
        """Handle network disconnection - re-enable connect so user can retry"""
        if hasattr(self, 'network_status_label'):
            self.network_status_label.setText("Status: Disconnected")
        if hasattr(self, 'connect_button'):
            self.connect_button.setEnabled(True)
        if hasattr(self, 'server_host_input'):
            self.server_host_input.setEnabled(True)
        if hasattr(self, 'server_port_spin'):
            self.server_port_spin.setEnabled(True)
        if hasattr(self, 'disconnect_button'):
            self.disconnect_button.setEnabled(False)
    
    def on_network_game_created(self, data):
        """Handle game creation on server - close current window and create new gameplay window"""
        logger.debug("on_network_game_created called with data: %s", data.keys())
        self.network_session_id = data.get('session_id')
        assigned_player_id = data.get('player_id', 0)
        
        logger.debug("Creating game window for player %s, session %s", assigned_player_id, self.network_session_id)
        
        # Deserialize and set up local game state
        game_state = data.get('game_state', {})
        if not game_state:
            logger.error("No game_state in data")
            QMessageBox.warning(self, "Error", "No game state received from server")
            return
        
        logger.debug("Received game_state keys: %s", list(game_state.keys()))
        try:
            game = self.network_client.deserialize_game_state(game_state)
            logger.debug("deserialize_game_state returned: %s, type: %s", game, type(game))
            if game is None:
                error_msg = ("Failed to deserialize game state from server.\n\n"
                            "Check console output for detailed error information.")
                logger.error("deserialize_game_state returned None")
                QMessageBox.warning(self, "Deserialization Error", error_msg)
                return
            if not hasattr(game, 'get_players'):
                error_msg = ("Deserialized object is not a valid Game object.\n\n"
                            f"Type: {type(game)}\n"
                            "Check console output for detailed error information.")
                logger.error("Deserialized object missing get_players method. Type: %s", type(game))
                QMessageBox.warning(self, "Deserialization Error", error_msg)
                return
        except Exception as e:
            error_msg = f"Exception during deserialization: {e}\n\nCheck console for details."
            logger.error("Exception in on_network_game_created during deserialization: %s", e)
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Deserialization Error", error_msg)
            return
        
        logger.debug("Game deserialized successfully, %d players", len(game.get_players()))
        
        # Store network client and thread references before closing
        network_client = self.network_client
        network_thread = self.network_thread
        network_session_id = self.network_session_id
        all_players_info = data.get('all_players', [])
        
        # Get QApplication instance
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            logger.error("No QApplication instance found")
            return
        
        logger.debug("Creating MainWindow for networked game...")
        # Create new gameplay window BEFORE closing old one (to keep references alive)
        try:
            new_window = MainWindow()
            new_window.setWindowTitle("Railroad Baron - Networked")
            new_window.setGeometry(100, 100, 800, 600)
            new_window.start_networked_game(
                game=game,
                assigned_player_id=assigned_player_id,
                network_client=network_client,
                network_thread=network_thread,
                network_session_id=network_session_id,
                all_players_info=all_players_info
            )
            logger.debug("Networked game window created successfully")
        except Exception as e:
            logger.error("Error creating networked game window: %s", e)
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Error", f"Failed to create game window: {str(e)}")
            return
        
        # Show new window
        new_window.show()
        new_window.raise_()
        new_window.activateWindow()
        logger.debug("New window shown")
        
        # Store reference to prevent garbage collection
        if not hasattr(app, '_network_windows'):
            app._network_windows = []
        app._network_windows.append(new_window)
        
        # Close current window AFTER new window is shown
        logger.debug("Closing old window...")
        self.close()
        
        logger.debug("Game window created for player %s", assigned_player_id)
    
    def on_network_game_state_received(self, data):
        """Handle game state update from server (called on GameLobbyWindow before game starts)"""
        game_state = data.get('game_state', {})
        game = self.network_client.deserialize_game_state(game_state)
        
        if game and hasattr(self, 'operations') and self.operations:
            # Update local game state - this updates marketplace, players, lines, etc.
            self.operations.game = game
            game_map = game.get_map()
            if game_map and hasattr(self.operations, '_session_deleted_population_points') and self.operations._session_deleted_population_points:
                if not hasattr(game_map, '_deleted_population_points'):
                    game_map._deleted_population_points = set()
                game_map._deleted_population_points |= self.operations._session_deleted_population_points
                if hasattr(game_map, 'population') and game_map.population is not None:
                    for pt in self.operations._session_deleted_population_points:
                        game_map.population.pop(pt, None)
            
            # Update year display immediately
            if hasattr(self, 'year_display_label'):
                try:
                    self.year_display_label.setText(f"Year: {game.get_year()}")
                except:
                    pass
            
            # Refresh ALL displays to show updated marketplace stock, income controls, etc.
            self.refresh_all_displays()
            
            # Don't show popups for every game state update (too spammy)
            # The UI refresh is enough to show the changes
    
    def on_network_error(self, message):
        """Handle network error - re-enable connect so user can retry"""
        QMessageBox.warning(self, "Network Error", message)
        if hasattr(self, 'network_status_label'):
            self.network_status_label.setText(f"Status: Error - {message[:30]}")
        # Re-enable connect button so player can attempt to connect again
        if hasattr(self, 'connect_button'):
            self.connect_button.setEnabled(True)
        if hasattr(self, 'server_host_input'):
            self.server_host_input.setEnabled(True)
        if hasattr(self, 'server_port_spin'):
            self.server_port_spin.setEnabled(True)
    
    def on_network_player_joined(self, data):
        """Handle player joining"""
        player_name = data.get('player_name')
        total_players = data.get('total_players')
        QMessageBox.information(self, "Player Joined", f"{player_name} joined the game. Total players: {total_players}")
        # Update players tab
        self.update_players_tab()
    
    def rebuild_player_name_fields(self, count):
        """Override to sync player names with server after rebuilding"""
        super().rebuild_player_name_fields(count)
        # Re-setup sync for the new name fields
        if hasattr(self, '_setup_player_name_sync'):
            self._setup_player_name_sync()
    
    def start_game_with_players(self, *args, **kwargs):
        """Override to remove player selector from Actions tab in network mode"""
        super().start_game_with_players(*args, **kwargs)
        
        # Remove player selector from Actions tab if in network mode
        if self.is_network_mode and hasattr(self, 'info_layout') and hasattr(self, 'player_combo'):
            # Find and remove the player selector layout
            for i in reversed(range(self.info_layout.count())):
                item = self.info_layout.itemAt(i)
                if item:
                    widget = item.widget()
                    if widget == self.player_combo:
                        self.info_layout.removeWidget(widget)
                        widget.setParent(None)
                        # Also remove the label before it
                        if i > 0:
                            prev_item = self.info_layout.itemAt(i - 1)
                            if prev_item:
                                prev_widget = prev_item.widget()
                                if isinstance(prev_widget, QLabel) and "Current Player" in prev_widget.text():
                                    self.info_layout.removeWidget(prev_widget)
                                    prev_widget.setParent(None)
                        break
                    layout = item.layout()
                    if layout:
                        # Check if this layout contains the player combo
                        for j in reversed(range(layout.count())):
                            layout_item = layout.itemAt(j)
                            if layout_item:
                                layout_widget = layout_item.widget()
                                if layout_widget == self.player_combo:
                                    layout.removeWidget(layout_widget)
                                    layout_widget.setParent(None)
                                    # Remove the entire layout if it's now empty or only has label
                                    if layout.count() <= 1:
                                        self.info_layout.removeItem(layout)
                                    break
        
        # Update players tab
        self.update_players_tab()
    
    def setup_settings_sync(self):
        """Setup synchronization of game settings with server"""
        if not hasattr(self, 'is_network_mode') or not self.is_network_mode or not self.network_client:
            return
        
        # Track if we're updating from server (to prevent loops)
        self._updating_from_server = False
        
        # Connect setting widgets to send updates to server
        if hasattr(self, 'map_width_spin'):
            self.map_width_spin.valueChanged.connect(
                lambda v: self._send_setting_update('map_width', v) if not self._updating_from_server else None
            )
        if hasattr(self, 'map_height_spin'):
            self.map_height_spin.valueChanged.connect(
                lambda v: self._send_setting_update('map_height', v) if not self._updating_from_server else None
            )
        if hasattr(self, 'player_count_spin'):
            self.player_count_spin.valueChanged.connect(
                lambda v: self._send_setting_update('player_count', v) if not self._updating_from_server else None
            )
        if hasattr(self, 'starting_money_spin'):
            self.starting_money_spin.valueChanged.connect(
                lambda v: self._send_setting_update('starting_money', v) if not self._updating_from_server else None
            )
        if hasattr(self, 'replenishment_rate_spin'):
            self.replenishment_rate_spin.valueChanged.connect(
                lambda v: self._send_setting_update('worker_replenishment_rate', v) if not self._updating_from_server else None
            )
        
        # Player name edits - need to track them dynamically
        self._setup_player_name_sync()
    
    def _setup_player_name_sync(self):
        """Setup sync for player name fields"""
        if not hasattr(self, 'is_network_mode') or not self.is_network_mode or not hasattr(self, 'name_edits'):
            return
        
        for i, name_edit in enumerate(self.name_edits):
            if name_edit:
                # Disconnect existing connections
                try:
                    name_edit.textChanged.disconnect()
                except:
                    pass
                # Connect to send updates
                name_edit.textChanged.connect(
                    lambda text, idx=i: self._send_player_name_update(idx, text) if not self._updating_from_server else None
                )
    
    def _send_setting_update(self, setting_name: str, value):
        """Send setting update to server"""
        if self.network_client and self.network_client.connected_flag:
            self.network_client.update_setting(setting_name, value)
    
    def _send_player_name_update(self, index: int, name: str):
        """Send player name update to server"""
        if self.network_client and self.network_client.connected_flag:
            # Get current player names
            if hasattr(self, 'name_edits'):
                player_names = []
                for edit in self.name_edits:
                    if edit:
                        player_names.append(edit.text().strip() if edit.text().strip() else f"Player{len(player_names) + 1}")
                    else:
                        player_names.append(f"Player{len(player_names) + 1}")
                # Update the specific name
                if index < len(player_names):
                    player_names[index] = name.strip() if name.strip() else f"Player{index + 1}"
                self.network_client.update_setting('player_names', player_names)
    
    def on_settings_updated(self, data: dict):
        """Handle settings update from server"""
        self._updating_from_server = True
        try:
            if 'map_width' in data and hasattr(self, 'map_width_spin'):
                self.map_width_spin.setValue(data['map_width'])
            if 'map_height' in data and hasattr(self, 'map_height_spin'):
                self.map_height_spin.setValue(data['map_height'])
            if 'player_count' in data and hasattr(self, 'player_count_spin'):
                self.player_count_spin.setValue(data['player_count'])
            if 'starting_money' in data and hasattr(self, 'starting_money_spin'):
                self.starting_money_spin.setValue(data['starting_money'])
            if 'worker_replenishment_rate' in data and hasattr(self, 'replenishment_rate_spin'):
                self.replenishment_rate_spin.setValue(data['worker_replenishment_rate'])
            if 'player_names' in data and hasattr(self, 'name_edits'):
                player_names = data['player_names']
                # Update name fields
                for i, name in enumerate(player_names):
                    if i < len(self.name_edits) and self.name_edits[i]:
                        self.name_edits[i].setText(name)
                # Re-setup sync after updating names
                self._setup_player_name_sync()
        finally:
            self._updating_from_server = False
    
    def refresh_all_displays(self):
        """Refresh all UI displays after game state update. Uses same logic as PC version."""
        super().refresh_all_displays()  # DisplayMapInfo, marketplace, construction, status bar, government, lines, etc.
        self.update_players_tab()  # Networked-specific: sync player names with server


if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    dialog = GameModeDialog()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        if dialog.choice == 'local':
            window = MainWindow()
            window.show()
        elif dialog.choice == 'network':
            window = NetworkedMainWindow(network_mode=True)
            window.show()
        else:
            sys.exit(0)
    else:
        sys.exit(0)
    
    sys.exit(app.exec())
