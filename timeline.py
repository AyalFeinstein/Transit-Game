class Timeline:
    def __init__(self,
                 initial_year: int = 1830,
                 end_year: int = 2050):
        self.year = initial_year
        self.initial_year = initial_year
        self.end_year = end_year

    def get_year(self):
        return self.year

    def get_start_year(self):
        return self.initial_year

    def get_end_year(self):
        return self.end_year

    def advance(self):
        self.year += 1

    def go_back(self):
        self.year -= 1

    def reset(self):
        self.year = self.initial_year
