"""Custom vehicles for this ruleset.

Import the default vehicle catalog and add new Locomotive / Railcar / Ferry / Bus instances.
After saving, VEHICLES.md in this folder is regenerated automatically.
"""
from vehicle_presets import *  # noqa: F401, F403
from vehicle import Locomotive, Railcar
from game_resource import Coal, Wood, Oil, Electricity

# Example:
# my_engine = Locomotive(
#     name="Custom 0-4-0",
#     object_id=9001,
#     fuel=Coal(),
#     miles_per_unit=0.04,
#     top_speed=40,
#     average_speed=22,
#     maitnance_costs=40,
#     year_introduced=1840,
#     quality=1.5,
#     max_cars=4,
#     base_price=280,
# )
