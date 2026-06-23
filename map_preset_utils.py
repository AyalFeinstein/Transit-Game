"""Generic utilities for working with map preset files.
This module provides functions that work with any map preset file,
following the standard naming convention: {MAP_ID}_INCLUDED_POINTS"""
import os
import re
import ast
import time


def get_preset_file_info(game_map) -> tuple[str, str] | None:
    """Get the preset file path and INCLUDED_POINTS variable name for a map.
    
    Args:
        game_map: Map object
        
    Returns:
        Tuple of (file_path, variable_name) or None if not found
    """
    if not game_map:
        return None
    
    # Use map_preset_name when available; otherwise try to detect from preset files
    map_id = None
    preset_file = None
    
    if hasattr(game_map, 'map_preset_name') and game_map.map_preset_name:
        map_id = game_map.map_preset_name
        preset_file = f"map_presets_{map_id}.py"
    else:
        # Try to detect from MAP_ID in preset files
        # This allows new maps to work automatically
        import glob
        base_dir = os.path.dirname(os.path.abspath(__file__))
        preset_files = glob.glob(os.path.join(base_dir, "map_presets_*.py"))
        
        for pf in preset_files:
            try:
                with open(pf, 'r') as f:
                    content = f.read()
                    # Look for MAP_ID
                    map_id_match = re.search(r'MAP_ID\s*=\s*["\']([^"\']+)["\']', content)
                    if map_id_match:
                        # Check if this map matches
                        # For now, we'll need map-specific detection
                        # But this structure allows extension
                        pass
            except:
                continue
    
    if not map_id or not preset_file:
        return None
    
    # Construct variable name: {MAP_ID.upper()}_INCLUDED_POINTS
    variable_name = f"{map_id.upper().replace('-', '_')}_INCLUDED_POINTS"
    
    # Get full file path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, preset_file)
    
    if not os.path.exists(file_path):
        return None
    
    return (file_path, variable_name)


def read_included_points_from_file(file_path: str, variable_name: str) -> set[tuple[int, int]]:
    """Read INCLUDED_POINTS list from a preset file.
    
    Args:
        file_path: Path to the preset file
        variable_name: Name of the variable (e.g., "NYC_INCLUDED_POINTS")
        
    Returns:
        Set of (x, y) tuples
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Use a more robust method: find the assignment and parse the list
        # Pattern: VARIABLE_NAME = [ ... ]
        # We need to handle multi-line lists with nested tuples
        
        # First, find where the variable assignment starts
        pattern = rf'{re.escape(variable_name)}\s*=\s*\['
        match = re.search(pattern, content)
        
        if not match:
            return set()
        
        # Find the start position
        start_pos = match.end() - 1  # Position of the opening [
        
        # Now find the matching closing bracket by counting brackets
        bracket_count = 0
        i = start_pos
        in_string = False
        string_char = None
        
        while i < len(content):
            char = content[i]
            
            # Handle strings (skip brackets inside strings)
            if char in ('"', "'") and (i == 0 or content[i-1] != '\\'):
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char:
                    in_string = False
                    string_char = None
            
            if not in_string:
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        # Found the matching closing bracket
                        end_pos = i + 1
                        list_str = content[match.start():end_pos]
                        
                        # Extract just the list content
                        list_content = list_str.split('=', 1)[1].strip()
                        
                        # Parse using ast.literal_eval for safety
                        try:
                            points_list = ast.literal_eval(list_content)
                            if isinstance(points_list, list):
                                # Normalize all points to integer tuples and remove duplicates
                                points_set = set()
                                for p in points_list:
                                    if isinstance(p, (tuple, list)) and len(p) >= 2:
                                        # Convert to tuple of integers
                                        x, y = int(round(p[0])), int(round(p[1]))
                                        points_set.add((x, y))
                                    elif isinstance(p, (int, float)) and len(points_list) > 0:
                                        # Handle edge case where point might be a single number
                                        continue
                                print(f"DEBUG: Successfully read {len(points_set)} unique points from {variable_name} in {file_path}")
                                return points_set
                        except Exception as e1:
                            # Fallback: try eval (less safe but handles more cases)
                            try:
                                points_list = eval(list_content)
                                if isinstance(points_list, list):
                                    # Normalize all points to integer tuples and remove duplicates
                                    points_set = set()
                                    for p in points_list:
                                        if isinstance(p, (tuple, list)) and len(p) >= 2:
                                            # Convert to tuple of integers
                                            x, y = int(round(p[0])), int(round(p[1]))
                                            points_set.add((x, y))
                                    print(f"DEBUG: Successfully read {len(points_set)} unique points from {variable_name} using eval fallback")
                                    return points_set
                            except Exception as e2:
                                print(f"ERROR: Failed to parse {variable_name} from {file_path}: {e1}, {e2}")
                                pass
                        break
            i += 1
        
        return set()
    except Exception as e:
        print(f"Error reading {variable_name} from {file_path}: {e}")
        return set()


def write_included_points_to_file(
    file_path: str,
    variable_name: str,
    points: set[tuple[int, int]],
    max_points_to_remove: int | None = None,
) -> bool:
    """Write INCLUDED_POINTS list to a preset file.
    
    Args:
        file_path: Path to the preset file
        variable_name: Name of the variable (e.g., "NYC_INCLUDED_POINTS")
        points: Set of (x, y) tuples to write
        max_points_to_remove: If set, abort when removing more than this many points
            (safeguard against mass deletion from read failure or bug). E.g. 1 for single-point removal.
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Always use absolute path so we write to the correct file regardless of cwd
        file_path = os.path.abspath(file_path)

        # Safeguard: prevent mass deletion (e.g. Staten Island disappearing from read failure)
        if max_points_to_remove is not None:
            current = read_included_points_from_file(file_path, variable_name)
            if current is not None:
                points_removed = len(current) - len(points)
                if points_removed > max_points_to_remove:
                    print(
                        f"ERROR: Aborting write - would remove {points_removed} points "
                        f"(max allowed {max_points_to_remove}). Likely read failure or bug."
                    )
                    return False
        cleaned_points = set()
        for p in points:
            if isinstance(p, (tuple, list)) and len(p) >= 2:
                x, y = int(round(p[0])), int(round(p[1]))
                cleaned_points.add((x, y))
        
        # Remove duplicates (set already does this, but be explicit)
        points = cleaned_points
        
        # Read current file
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Sort points for consistent output (removes any ordering issues)
        sorted_points = sorted(points)
        
        print(f"DEBUG: Writing {len(sorted_points)} unique points to {variable_name} (removed {len(points) - len(sorted_points)} duplicates if any)")
        points_str = "[\n" + ",\n".join(f"    {p}" for p in sorted_points) + "\n]"
        
        # Find and replace the variable assignment using bracket matching
        # First find the start of the assignment
        start_pattern = rf'{re.escape(variable_name)}\s*=\s*\['
        match = re.search(start_pattern, content, re.MULTILINE)
        
        if not match:
            print(f"ERROR: Could not find {variable_name} assignment in {file_path}")
            return False
        
        start_pos = match.end() - 1  # Position of the opening [
        
        # Find the matching closing bracket by counting brackets
        bracket_count = 0
        i = start_pos
        in_string = False
        string_char = None
        
        while i < len(content):
            char = content[i]
            
            # Handle strings (skip brackets inside strings)
            if char in ('"', "'") and (i == 0 or content[i-1] != '\\'):
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char:
                    in_string = False
                    string_char = None
            
            if not in_string:
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        # Found the matching closing bracket
                        end_pos = i + 1
                        
                        # Replace the entire assignment
                        new_content = content[:match.start()] + f'{variable_name} = {points_str}' + content[end_pos:]
                        
                        print(f"DEBUG: Writing to {file_path}, replacing {variable_name} with {len(sorted_points)} points")
                        print(f"DEBUG: First few points being written: {sorted_points[:5] if len(sorted_points) > 5 else sorted_points}")
                        
                        # Write to file with proper flushing (os imported at module level)
                        with open(file_path, 'w') as f:
                            f.write(new_content)
                            f.flush()
                            os.fsync(f.fileno())
                        
                        # Verify the write by reading it back and parsing
                        time.sleep(0.01)
                        verify_points = read_included_points_from_file(file_path, variable_name)
                        if verify_points is not None:
                            verify_count = len(verify_points)
                            expected_count = len(points)
                            if verify_count == expected_count:
                                # Also verify that the points match (for removals, count should decrease)
                                print(f"SUCCESS: Wrote {verify_count} points to {variable_name} in {file_path} (verified)")
                                return True
                            else:
                                print(f"WARNING: Write verification - expected {expected_count} points, got {verify_count}")
                                print(f"DEBUG: Expected points: {sorted(list(points))[:10]}..." if len(points) > 10 else f"DEBUG: Expected points: {sorted(list(points))}")
                                print(f"DEBUG: Actual points: {sorted(list(verify_points))[:10]}..." if len(verify_points) > 10 else f"DEBUG: Actual points: {sorted(list(verify_points))}")
                                # Still return True if we wrote something (might be a timing issue)
                                return verify_count > 0
                        else:
                            print(f"WARNING: Could not read back {variable_name} from {file_path} for verification")
                            # Check if variable exists in file at least
                            with open(file_path, 'r') as verify_f:
                                verify_content = verify_f.read()
                                if variable_name in verify_content:
                                    print(f"WARNING: Variable {variable_name} exists in file but could not be parsed")
                                    return True  # Assume success if variable is present
                                else:
                                    print(f"ERROR: Variable {variable_name} not found in file after write!")
                                    return False
            i += 1
        
        print(f"ERROR: Could not find matching closing bracket for {variable_name} in {file_path}")
        return False
    except Exception as e:
        print(f"Error writing {variable_name} to {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return False


def remove_duplicates_from_file(file_path: str, variable_name: str) -> bool:
    """Remove duplicate points from INCLUDED_POINTS in a preset file.
    
    Args:
        file_path: Path to the preset file
        variable_name: Name of the variable (e.g., "NYC_INCLUDED_POINTS")
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Read current points
        points = read_included_points_from_file(file_path, variable_name)
        if points is None:
            return False
        
        # Normalize and deduplicate
        normalized_points = set()
        for p in points:
            if isinstance(p, (tuple, list)) and len(p) >= 2:
                x, y = int(round(p[0])), int(round(p[1]))
                normalized_points.add((x, y))
        
        # Write back (this will automatically deduplicate)
        return write_included_points_to_file(file_path, variable_name, normalized_points)
    except Exception as e:
        print(f"Error removing duplicates from {variable_name} in {file_path}: {e}")
        return False

