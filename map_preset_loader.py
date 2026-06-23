"""Dynamic Map Preset Loader: discover and load map presets by name.
Presets expose a generic interface (get_bounds, get_timeline, get_display_name, etc.)
so the game never needs to know the map name."""

import importlib
import importlib.util
import sys
from typing import Optional, Dict, Any
from pathlib import Path

class MapPresetRegistry:
    """Registry for discovering and loading map presets dynamically."""
    
    def __init__(self):
        self._presets: Dict[str, Any] = {}
        self._discover_presets()
    
    def _discover_presets(self):
        """Discover all map preset files in the current directory."""
        # Get the directory where this file is located (should be project root)
        current_dir = Path(__file__).parent
        
        # Look for all map_presets_*.py files
        for preset_file in current_dir.glob("map_presets_*.py"):
            # Skip template and backup files
            if "template" in preset_file.stem.lower() or preset_file.stem.endswith("_backup"):
                continue
            
            # Extract preset name (e.g., "nyc" from "map_presets_nyc.py")
            preset_name = preset_file.stem.replace("map_presets_", "")
            
            try:
                # Load the preset module
                module_name = preset_file.stem
                if module_name in sys.modules:
                    # Reload if already loaded
                    module = importlib.reload(sys.modules[module_name])
                else:
                    spec = importlib.util.spec_from_file_location(module_name, preset_file)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                
                # Check if it's a valid preset (has required functions/variables)
                if self._is_valid_preset(module):
                    self._presets[preset_name] = {
                        'module': module,
                        'file': preset_file,
                        'name': preset_name
                    }
                    print(f"Discovered map preset: {preset_name}")
            except Exception as e:
                print(f"Warning: Failed to load map preset {preset_name}: {e}")
    
    def _is_valid_preset(self, module: Any) -> bool:
        """Check if a module is a valid map preset.
        
        A valid preset must have either:
        1. Functions: is_*_map, get_*_bounds, get_*_timeline (for location-based maps)
        2. Variables: MAP_BOUNDS, MAP_TIMELINE (for template-based maps)
        """
        module_name = module.__name__
        preset_name = module_name.replace("map_presets_", "")
        
        # Check for location-based preset
        has_is_map = hasattr(module, f"is_{preset_name}_map") or hasattr(module, "is_map")
        has_bounds = hasattr(module, f"get_{preset_name}_bounds") or hasattr(module, "get_bounds")
        has_timeline = hasattr(module, f"get_{preset_name}_timeline") or hasattr(module, "get_timeline")
        
        # Check for template-based preset
        has_map_bounds = hasattr(module, "MAP_BOUNDS")
        has_map_timeline = hasattr(module, "MAP_TIMELINE")
        
        return (has_is_map and has_bounds and has_timeline) or (has_map_bounds and has_map_timeline)
    
    def get_preset(self, preset_name: str) -> Optional[Any]:
        """Get a preset module by name.
        
        Args:
            preset_name: Name of the preset (e.g., "nyc", "ny_metro", "custom")
            
        Returns:
            The preset module, or None if not found
        """
        return self._presets.get(preset_name, {}).get('module')
    
    def list_presets(self) -> list[str]:
        """List all available preset names."""
        return list(self._presets.keys())
    
    def get_preset_info(self, preset_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a preset."""
        return self._presets.get(preset_name)


# Global registry instance
_registry: Optional[MapPresetRegistry] = None


def get_registry() -> MapPresetRegistry:
    """Get the global map preset registry."""
    global _registry
    if _registry is None:
        _registry = MapPresetRegistry()
    return _registry


def load_map_preset(preset_name: str) -> Optional[Any]:
    """Load a map preset by name.
    
    Args:
        preset_name: Name of the preset (e.g., "nyc", "ny_metro", "custom")
        
    Returns:
        The preset module, or None if not found
    """
    return get_registry().get_preset(preset_name)


def reload_map_preset_from_disk(preset_name: str) -> Optional[Any]:
    """Force reload a map preset from disk. Use after edit-mode changes to INCLUDED_POINTS.
    Ensures the registry and all imports see the updated file contents."""
    registry = get_registry()
    preset_info = registry._presets.get(preset_name)
    if not preset_info:
        return None
    module_name = f"map_presets_{preset_name}"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
        registry._presets[preset_name]['module'] = module
        return module
    return registry.get_preset(preset_name)


def list_available_presets() -> list[str]:
    """List all available map preset names."""
    return get_registry().list_presets()


def preset_has_water_areas(preset_name: str) -> bool:
    """Check if a preset provides custom water detection (is_water_location)."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    return preset is not None and hasattr(preset, 'is_water_location')


def preset_has_population_init(preset_name: str) -> bool:
    """Check if a preset provides custom population initialization."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    if not preset:
        return False
    if hasattr(preset, 'get_population_at_location'):
        return True
    return any(
        attr.startswith('initialize_') and attr.endswith('_population')
        for attr in dir(preset) if isinstance(attr, str)
    )


def is_map_preset(preset_name: str, map_center_lat: float, map_center_lon: float, width: int, height: int) -> bool:
    """Check if a location matches a map preset.
    
    Args:
        preset_name: Name of the preset to check
        map_center_lat: Center latitude
        map_center_lon: Center longitude
        width: Map width
        height: Map height
        
    Returns:
        True if the location matches the preset, False otherwise
    """
    preset = load_map_preset(preset_name)
    if preset is None:
        return False
    if hasattr(preset, "is_map"):
        try:
            return preset.is_map(map_center_lat, map_center_lon, width, height)
        except Exception:
            pass
    for func_name in [f"is_{preset_name}_map", "is_map"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)(map_center_lat, map_center_lon, width, height)
            except Exception:
                pass
    if hasattr(preset, "MAP_BOUNDS"):
        try:
            b = preset.MAP_BOUNDS
            return (b['min_lat'] <= map_center_lat <= b['max_lat'] and
                    b['min_lon'] <= map_center_lon <= b['max_lon'])
        except Exception:
            pass
    return False


def get_map_bounds(preset_name: str) -> Optional[Dict[str, Any]]:
    """Get map bounds for a preset.
    
    Args:
        preset_name: Name of the preset
        
    Returns:
        Dictionary with bounds, or None if not found
    """
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    
    # Try standard function names
    for func_name in [f"get_{preset_name}_bounds", "get_bounds"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    
    # Try MAP_BOUNDS variable
    if hasattr(preset, "MAP_BOUNDS"):
        return preset.MAP_BOUNDS
    
    return None


def get_map_timeline(preset_name: str) -> Optional[Dict[str, Any]]:
    """Get timeline settings for a preset.
    
    Args:
        preset_name: Name of the preset
        
    Returns:
        Dictionary with timeline settings, or None if not found
    """
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_timeline"):
        try:
            return preset.get_timeline()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_timeline", "get_timeline"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    if hasattr(preset, "MAP_TIMELINE"):
        return preset.MAP_TIMELINE
    return None


def get_map_display_name(preset_name: str) -> Optional[str]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_display_name"):
        try:
            return preset.get_display_name()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_map_display_name", "get_display_name"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    if hasattr(preset, "MAP_NAME"):
        return preset.MAP_NAME
    return None


def get_map_zoom(preset_name: str) -> Optional[Dict[str, Any]]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_zoom"):
        try:
            return preset.get_zoom()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_zoom", "get_zoom"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    if hasattr(preset, "MAP_ZOOM"):
        return preset.MAP_ZOOM
    return None


def get_default_center_lat(preset_name: str) -> Optional[float]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_default_center_lat"):
        try:
            return preset.get_default_center_lat()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_default_center_lat", "get_default_center_lat"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    b = get_map_bounds(preset_name)
    return b.get("center_lat") if b else None


def get_default_center_lon(preset_name: str) -> Optional[float]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_default_center_lon"):
        try:
            return preset.get_default_center_lon()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_default_center_lon", "get_default_center_lon"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    b = get_map_bounds(preset_name)
    return b.get("center_lon") if b else None
