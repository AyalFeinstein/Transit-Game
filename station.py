class Station:
    """Represents a passenger/cargo station on the rail network"""
    
    def __init__(self, name: str, x: float, y: float, object_id: int = None, line_names: list = None):
        self.name = name
        self.x = x
        self.y = y
        self.object_id = object_id
        self.line_names = line_names if line_names is not None else []
        self.quality = 1.0
        self.maintenance_workers = 0
    
    def update_quality(self):
        """Update quality based on maintenance workers (1.0 base + 0.1 per worker, max 3.0)"""
        self.quality = min(3.0, 1.0 + (self.maintenance_workers * 0.1))
    
    def add_line(self, line_name: str):
        """Add a line to this station's service"""
        if line_name not in self.line_names:
            self.line_names.append(line_name)
    
    def get_line_names(self):
        """Get list of line names this station serves"""
        return self.line_names
    
    def serves_line(self, line_name: str) -> bool:
        """Check if this station serves a given line"""
        return line_name in self.line_names
        
    def get_name(self):
        return self.name
    
    def get_position(self):
        return (self.x, self.y)
    
    def get_x(self):
        return self.x
    
    def get_y(self):
        return self.y
    
    def get_object_id(self):
        return self.object_id
    
    def __repr__(self):
        return f"Station({self.name} at ({self.x}, {self.y}))"
