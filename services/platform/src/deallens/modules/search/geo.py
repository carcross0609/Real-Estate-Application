"""Pure geometry & map-tile helpers — no I/O, no database, fully unit-testable.

Two jobs:
- **Tiles (§12.2).** Web-Mercator XYZ ↔ lon/lat math so the tile endpoint can turn a
  `{z,x,y}` request into the bounding box PostGIS filters on, and a low-zoom grid-clusterer so
  we return cluster centroids instead of millions of pins.
- **Geometry → WKT.** Turn a validated viewport / drawn polygon / radius into the WKT string
  the service hands to `ST_GeomFromText` (as a *bound parameter* — the values here are numeric,
  never interpolated into SQL). Real topology (intersection, containment) is Postgres's job;
  this layer only shapes and sanity-checks the geometry.
"""

import math

from deallens.modules.search.schemas import (
    BBox,
    GeoJSONGeometry,
    RadiusGeo,
    TileCluster,
    TileFeature,
)

MAX_TILE_ZOOM = 24
# Below this zoom a tile covers too much ground to send individual pins — cluster instead.
CLUSTER_ZOOM_THRESHOLD = 13
# A clustered tile is diced into a 2^GRID_BITS × 2^GRID_BITS lattice; points sharing a cell
# collapse to one cluster. 4 → 16×16 = up to 256 clusters/tile, plenty for a pin map.
_GRID_BITS = 4


def _lon_at(x: float, n: int) -> float:
    return x / n * 360.0 - 180.0


def _lat_at(y: float, n: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def tile_to_bbox(z: int, x: int, y: int) -> BBox:
    """XYZ tile → its WGS84 bounding box. Raises `ValueError` on an out-of-range tile so the
    router returns 422, not a silently-wrong extent.
    """
    if not 0 <= z <= MAX_TILE_ZOOM:
        raise ValueError(f"zoom {z} out of range 0..{MAX_TILE_ZOOM}")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise ValueError(f"tile ({x},{y}) out of range for zoom {z} (0..{n - 1})")
    return BBox(
        min_lon=_lon_at(x, n),
        min_lat=_lat_at(y + 1, n),  # y grows southward → y+1 is the southern (min) edge
        max_lon=_lon_at(x + 1, n),
        max_lat=_lat_at(y, n),
    )


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """WGS84 point → the XYZ tile containing it (inverse of `tile_to_bbox`, for tests/tools)."""
    n = 1 << z
    lat_rad = math.radians(lat)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * n)
    x = min(max(x, 0), n - 1)
    y = min(max(y, 0), n - 1)
    return x, y


def should_cluster(z: int) -> bool:
    return z < CLUSTER_ZOOM_THRESHOLD


def _format_num(v: float) -> str:
    # WKT coordinate: fixed enough precision for ~cm at the equator, no scientific notation.
    return f"{v:.7f}"


def bbox_to_wkt(bbox: BBox) -> str:
    """A rectangle as a closed WKT polygon (ring is CCW, first == last)."""
    x0, y0, x1, y1 = bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    coords = ", ".join(f"{_format_num(lon)} {_format_num(lat)}" for lon, lat in ring)
    return f"POLYGON(({coords}))"


def _position(pos: object) -> tuple[float, float]:
    if not isinstance(pos, list | tuple) or len(pos) < 2:
        raise ValueError("each coordinate must be a [lon, lat] pair")
    lon, lat = pos[0], pos[1]
    if not isinstance(lon, int | float) or not isinstance(lat, int | float):
        raise ValueError("coordinate values must be numbers")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"coordinate ({lon}, {lat}) out of WGS84 range")
    return float(lon), float(lat)


def _ring_wkt(ring: object) -> str:
    if not isinstance(ring, list) or len(ring) < 3:
        raise ValueError("a polygon ring needs at least 3 positions")
    pts = [_position(p) for p in ring]
    if pts[0] != pts[-1]:  # close the ring if the client left it open
        pts.append(pts[0])
    if len(pts) < 4:
        raise ValueError("a polygon ring needs at least 3 distinct positions")
    return "(" + ", ".join(f"{_format_num(lon)} {_format_num(lat)}" for lon, lat in pts) + ")"


def _polygon_wkt_body(rings: object) -> str:
    if not isinstance(rings, list) or not rings:
        raise ValueError("a polygon needs at least an outer ring")
    return "(" + ", ".join(_ring_wkt(r) for r in rings) + ")"


def geojson_to_wkt(geometry: GeoJSONGeometry) -> str:
    """A drawn Polygon/MultiPolygon → WKT. Validates position shape and coordinate ranges and
    closes open rings; leaves self-intersection/winding checks to PostGIS (`ST_IsValid`).
    """
    coords = geometry.coordinates
    if geometry.type == "Polygon":
        return "POLYGON" + _polygon_wkt_body(coords)
    # MultiPolygon: a list of polygons.
    if not isinstance(coords, list) or not coords:
        raise ValueError("a MultiPolygon needs at least one polygon")
    return "MULTIPOLYGON(" + ", ".join(_polygon_wkt_body(p) for p in coords) + ")"


def radius_point_wkt(radius: RadiusGeo) -> str:
    """The centre point as WKT; the service pairs it with `radius.radius_m` in an
    `ST_DWithin(geom::geography, point::geography, m)` predicate.
    """
    return f"POINT({_format_num(radius.lon)} {_format_num(radius.lat)})"


def cluster_features(z: int, features: list[TileFeature]) -> list[TileCluster]:
    """Grid-cluster pins for a low-zoom tile. Points snapping to the same lattice cell collapse
    to one cluster at their centroid; `avg_score_bucket` is the rounded mean of members that
    carry one. Deterministic (no order dependence beyond arithmetic) so tiles are cacheable.
    """
    if not features:
        return []
    n = 1 << z
    cell = 360.0 / (n * (1 << _GRID_BITS))  # degrees per lattice cell at this zoom

    buckets: dict[tuple[int, int], list[TileFeature]] = {}
    for f in features:
        key = (math.floor(f.lon / cell), math.floor(f.lat / cell))
        buckets.setdefault(key, []).append(f)

    clusters: list[TileCluster] = []
    for members in buckets.values():
        count = len(members)
        lon = sum(m.lon for m in members) / count
        lat = sum(m.lat for m in members) / count
        score_buckets = [m.score_bucket for m in members if m.score_bucket is not None]
        avg = round(sum(score_buckets) / len(score_buckets)) if score_buckets else None
        clusters.append(TileCluster(lon=lon, lat=lat, count=count, avg_score_bucket=avg))
    return clusters


def decile_bucket(value: float, lo: float, hi: float) -> int:
    """Map a value in [lo, hi] to a 0..9 bucket. Used to coarsen score/price on tiles so the
    exact figure never leaves the server (§12.2). Out-of-range values clamp to the end bucket.
    """
    if hi <= lo:
        return 0
    frac = (value - lo) / (hi - lo)
    return min(9, max(0, int(frac * 10)))
