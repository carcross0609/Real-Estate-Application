"""Pure geometry / tile-math tests — no database (run everywhere, incl. the sandbox)."""

import math

import pytest

from deallens.modules.search import geo
from deallens.modules.search.schemas import BBox, GeoJSONGeometry, RadiusGeo, TileFeature


def test_tile_zero_is_whole_world() -> None:
    bbox = geo.tile_to_bbox(0, 0, 0)
    assert bbox.min_lon == pytest.approx(-180.0)
    assert bbox.max_lon == pytest.approx(180.0)
    # Web Mercator clips latitude at ±85.0511°.
    assert bbox.max_lat == pytest.approx(85.0511, abs=1e-3)
    assert bbox.min_lat == pytest.approx(-85.0511, abs=1e-3)


def test_tile_bbox_min_corner_is_south_west() -> None:
    # y grows southward, so a higher y is further south (smaller latitude).
    top = geo.tile_to_bbox(4, 5, 5)
    bottom = geo.tile_to_bbox(4, 5, 6)
    assert bottom.max_lat < top.max_lat
    assert top.min_lon == pytest.approx(top.min_lon)  # sanity
    assert top.min_lat < top.max_lat


def test_tile_roundtrip_point_lands_in_its_tile() -> None:
    z, lon, lat = 12, -122.4194, 37.7749  # San Francisco
    x, y = geo.lonlat_to_tile(lon, lat, z)
    bbox = geo.tile_to_bbox(z, x, y)
    assert bbox.min_lon <= lon <= bbox.max_lon
    assert bbox.min_lat <= lat <= bbox.max_lat


@pytest.mark.parametrize("z,x,y", [(-1, 0, 0), (2, 4, 0), (2, 0, 4), (30, 0, 0)])
def test_out_of_range_tiles_raise(z: int, x: int, y: int) -> None:
    with pytest.raises(ValueError):
        geo.tile_to_bbox(z, x, y)


def test_bbox_to_wkt_is_a_closed_ring() -> None:
    wkt = geo.bbox_to_wkt(BBox(min_lon=-1, min_lat=-2, max_lon=3, max_lat=4))
    assert wkt.startswith("POLYGON((") and wkt.endswith("))")
    coords = wkt[len("POLYGON((") : -2].split(", ")
    assert coords[0] == coords[-1]  # first == last → closed
    assert len(coords) == 5


def test_geojson_polygon_closes_open_ring() -> None:
    # An open ring (first != last) must be closed by the converter.
    geom = GeoJSONGeometry(
        type="Polygon", coordinates=[[[0, 0], [0, 1], [1, 1], [1, 0]]]
    )
    wkt = geo.geojson_to_wkt(geom)
    assert wkt.startswith("POLYGON((")
    pts = wkt[len("POLYGON((") : -2].split(", ")
    assert pts[0] == pts[-1]


def test_geojson_multipolygon() -> None:
    geom = GeoJSONGeometry(
        type="MultiPolygon",
        coordinates=[[[[0, 0], [0, 1], [1, 1], [0, 0]]], [[[5, 5], [5, 6], [6, 6], [5, 5]]]],
    )
    wkt = geo.geojson_to_wkt(geom)
    assert wkt.startswith("MULTIPOLYGON(((")


def test_geojson_rejects_out_of_range_and_short_rings() -> None:
    with pytest.raises(ValueError):
        geo.geojson_to_wkt(GeoJSONGeometry(type="Polygon", coordinates=[[[0, 0], [0, 200]]]))
    with pytest.raises(ValueError):
        geo.geojson_to_wkt(GeoJSONGeometry(type="Polygon", coordinates=[[[0, 0], [0, 1]]]))


def test_radius_point_wkt() -> None:
    wkt = geo.radius_point_wkt(RadiusGeo(lat=37.0, lon=-122.0, radius_m=1500))
    assert wkt.startswith("POINT(") and "-122.0" in wkt


def test_should_cluster_threshold() -> None:
    assert geo.should_cluster(geo.CLUSTER_ZOOM_THRESHOLD - 1)
    assert not geo.should_cluster(geo.CLUSTER_ZOOM_THRESHOLD)


def test_cluster_features_groups_nearby_and_splits_far() -> None:
    z = 8
    # Two clumps far apart at this zoom → two clusters; counts sum to input.
    features = [
        TileFeature(property_id=_uid(1), lon=0.0, lat=0.0, score_bucket=8),
        TileFeature(property_id=_uid(2), lon=0.0001, lat=0.0001, score_bucket=6),
        TileFeature(property_id=_uid(3), lon=40.0, lat=40.0, score_bucket=2),
    ]
    clusters = geo.cluster_features(z, features)
    assert len(clusters) == 2
    assert sum(c.count for c in clusters) == 3
    pair = next(c for c in clusters if c.count == 2)
    assert pair.avg_score_bucket == 7  # round((8+6)/2)


def test_cluster_features_empty() -> None:
    assert geo.cluster_features(5, []) == []


def test_decile_bucket_clamps() -> None:
    assert geo.decile_bucket(-5, 0, 100) == 0
    assert geo.decile_bucket(50, 0, 100) == 5
    assert geo.decile_bucket(1000, 0, 100) == 9
    assert geo.decile_bucket(10, 5, 5) == 0  # degenerate range


def _uid(n: int) -> "object":
    from uuid import UUID

    return UUID(int=n)


def test_lon_lat_monotonic() -> None:
    # Sanity: moving one tile east increases longitude, moving one tile south decreases lat.
    z = 6
    a = geo.tile_to_bbox(z, 10, 10)
    east = geo.tile_to_bbox(z, 11, 10)
    south = geo.tile_to_bbox(z, 10, 11)
    assert east.min_lon > a.min_lon
    assert south.max_lat < a.max_lat
    assert not math.isnan(a.min_lat)
