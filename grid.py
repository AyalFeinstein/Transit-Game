class Grid:
    pass


class GridPoint:
    def __init__(self,
                 x: float,
                 y: float):
        self.x = x
        self.y = y

    def __eq__(self, other):
        if type(other) == GridPoint:
            if other.x == self.x and other.y == self.y:
                return True
            else:
                return False
        else:
            return False


    def get_coordinates(self):
        return self.x, self.y


class GridSquare:
    def __init__(self,
                 top_left_coord: GridPoint,
                 top_right_coord: GridPoint,
                 bottom_left_coord: GridPoint,
                 bottom_right_coord: GridPoint):
        self.top_left_coord = top_left_coord
        self.top_right_coord = top_right_coord
        self.bottom_left_coord = bottom_left_coord
        self.bottom_right_coord = bottom_right_coord


class Water(GridSquare):
    def __init__(self,
                 top_left_coord: GridPoint,
                 top_right_coord: GridPoint,
                 bottom_left_coord: GridPoint,
                 bottom_right_coord: GridPoint):
        super().__init__(top_left_coord,
                 top_right_coord,
                 bottom_left_coord,
                 bottom_right_coord)

        self.area = self.calc_area()

    def calc_area(self):
        return self.top_left_coord.y * self.bottom_right_coord.x

    def get_area(self):
        return self.area


class Land(GridSquare):
    def __init__(self,
                 top_left_coord: GridPoint,
                 top_right_coord: GridPoint,
                 bottom_left_coord: GridPoint,
                 bottom_right_coord: GridPoint):
        super().__init__(top_left_coord,
                 top_right_coord,
                 bottom_left_coord,
                 bottom_right_coord)