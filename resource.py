class Resource:
    def __init__(self,
                 name):
        self.name = name


class Coal(Resource):
    def __init__(self):
        super().__init__('Coal')


class Wood(Resource):
    def __init__(self):
        super().__init__('Wood')


class Oil(Resource):
    def __init__(self):
        super().__init__('Oil')


class Electricity(Resource):
    def __init__(self):
        super().__init__('Electricity')

class Food(Resource):
    def __init__(self):
        super().__init__('Food')


