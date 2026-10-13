import json
from pathlib import Path
from typing import Any

TAMIL_NADU_BOUNDARY_PATH = Path(__file__).parent / "data" / "tamil_nadu.geojson"
with TAMIL_NADU_BOUNDARY_PATH.open(encoding="utf-8") as boundary_file:
    TAMIL_NADU_FEATURE: dict[str, Any] = json.load(boundary_file)


def _point_in_ring(longitude: float, latitude: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        cross = (longitude - x1) * (y2 - y1) - (latitude - y1) * (x2 - x1)
        if (
            abs(cross) <= 1e-10
            and min(x1, x2) - 1e-10 <= longitude <= max(x1, x2) + 1e-10
            and min(y1, y2) - 1e-10 <= latitude <= max(y1, y2) + 1e-10
        ):
            return True
        if (y1 > latitude) != (y2 > latitude):
            crossing_longitude = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < crossing_longitude:
                inside = not inside
        previous = current
    return inside


def is_in_tamil_nadu(latitude: float, longitude: float) -> bool:
    geometry = TAMIL_NADU_FEATURE["geometry"]
    polygons = geometry["coordinates"]
    for polygon in polygons:
        if not _point_in_ring(longitude, latitude, polygon[0]):
            continue
        if not any(_point_in_ring(longitude, latitude, hole) for hole in polygon[1:]):
            return True
    return False
