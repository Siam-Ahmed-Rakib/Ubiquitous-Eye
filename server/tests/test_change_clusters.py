"""Clustering changed pixels into reportable regions."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference.change_clusters import (
    ELLIPSE_SHRINK,
    ELLIPSE_SIGMA,
    MIN_CLUSTER_POINTS,
    ellipse_polygon,
    fit_change_clusters,
)


def _blob(centre, spread, n, seed):
    rng = np.random.default_rng(seed)
    return rng.normal(centre, spread, size=(n, 2))


def test_too_few_points_is_not_a_region():
    pts = _blob((90.4, 23.8), 0.001, MIN_CLUSTER_POINTS - 1, 0)
    assert fit_change_clusters(pts[:, 0], pts[:, 1]) == []


def test_one_contiguous_patch_stays_one_region():
    """The case a fixed k gets wrong: splitting a single clearing into quarters."""
    pts = _blob((90.40, 23.80), 0.002, 400, 1)

    clusters = fit_change_clusters(pts[:, 0], pts[:, 1])

    assert len(clusters) == 1
    assert clusters[0]["centerLon"] == pytest.approx(90.40, abs=0.001)
    assert clusters[0]["centerLat"] == pytest.approx(23.80, abs=0.001)


def test_three_separated_patches_are_found_separately():
    pts = np.vstack([
        _blob((90.30, 23.70), 0.002, 300, 2),
        _blob((90.50, 23.70), 0.002, 300, 3),
        _blob((90.40, 23.90), 0.002, 300, 4),
    ])

    clusters = fit_change_clusters(pts[:, 0], pts[:, 1])

    assert len(clusters) == 3
    centres = sorted((round(c["centerLon"], 2), round(c["centerLat"], 2))
                     for c in clusters)
    assert centres == [(90.30, 23.70), (90.40, 23.90), (90.50, 23.70)]


def test_clusters_come_back_largest_first():
    pts = np.vstack([
        _blob((90.30, 23.70), 0.002, 120, 5),
        _blob((90.50, 23.70), 0.002, 600, 6),
    ])

    clusters = fit_change_clusters(pts[:, 0], pts[:, 1])

    counts = [c["points"] for c in clusters]
    assert counts == sorted(counts, reverse=True)


def test_a_far_stray_does_not_drag_the_ellipse():
    """One misclassified cell must not stretch a region across the map."""
    core = _blob((90.40, 23.80), 0.002, 400, 7)
    with_stray = np.vstack([core, [[91.90, 24.90]]])

    clean = fit_change_clusters(core[:, 0], core[:, 1])
    dirty = fit_change_clusters(with_stray[:, 0], with_stray[:, 1])

    assert len(dirty) == 1
    assert dirty[0]["centerLon"] == pytest.approx(clean[0]["centerLon"], abs=0.002)
    assert dirty[0]["semiMajorDeg"] == pytest.approx(
        clean[0]["semiMajorDeg"], rel=0.25)


def test_an_elongated_patch_produces_elongated_ellipses():
    """A river losing water is long and thin, and should look it.

    A long corridor may be reported as several regions along its length — that
    is a more useful answer than one ellipse spanning the whole river — but
    every one of them must still lie along the corridor rather than across it.
    """
    rng = np.random.default_rng(8)
    lon = rng.uniform(90.30, 90.50, 500)
    lat = 23.80 + rng.normal(0, 0.0015, 500)

    clusters = fit_change_clusters(lon, lat)

    assert clusters, 'a clear corridor must be reported'
    for c in clusters:
        assert c["semiMajorDeg"] > 2 * c["semiMinorDeg"], 'should be a slot, not a disc'
        # Running east-west, so every major axis is near horizontal.
        assert abs(((c["angleDeg"] + 90) % 180) - 90) < 15
    # Together they span the corridor rather than clustering at one end.
    centres = sorted(c["centerLon"] for c in clusters)
    assert centres[-1] - centres[0] > 0.05 or len(clusters) == 1


def test_the_polygon_traces_the_ellipse_it_describes():
    pts = _blob((90.40, 23.80), 0.002, 400, 9)
    cluster = fit_change_clusters(pts[:, 0], pts[:, 1])[0]

    ring = ellipse_polygon(cluster)

    assert ring.shape == (72, 2)
    # Every vertex sits between the minor and major radius of the centre.
    d = np.hypot(ring[:, 0] - cluster["centerLon"], ring[:, 1] - cluster["centerLat"])
    assert d.max() == pytest.approx(cluster["semiMajorDeg"], rel=1e-6)
    assert d.min() == pytest.approx(cluster["semiMinorDeg"], rel=1e-6)


def test_results_are_stable_across_runs():
    """Same pixels, same regions — a report that changes on re-run is useless."""
    pts = np.vstack([
        _blob((90.30, 23.70), 0.002, 300, 10),
        _blob((90.50, 23.75), 0.002, 300, 11),
    ])

    first = fit_change_clusters(pts[:, 0], pts[:, 1])
    second = fit_change_clusters(pts[:, 0], pts[:, 1])

    assert [c["centerLon"] for c in first] == [c["centerLon"] for c in second]
    assert [c["points"] for c in first] == [c["points"] for c in second]


def test_the_ellipse_is_shrunk_to_mark_the_core_not_the_extent():
    """Full-size ellipses over a large change area cover the ground they describe."""
    spread = 0.002
    pts = _blob((90.40, 23.80), spread, 800, 20)

    cluster = fit_change_clusters(pts[:, 0], pts[:, 1])[0]

    # A circular blob of this spread would reach ELLIPSE_SIGMA * spread at full
    # size; the shrink pulls it in by a known proportion.
    full = ELLIPSE_SIGMA * spread
    assert cluster["semiMajorDeg"] == pytest.approx(
        full * ELLIPSE_SHRINK, rel=0.15)
    assert cluster["semiMajorDeg"] < full, 'must be smaller than the raw 2-sigma'


def test_shrinking_does_not_move_the_reported_centre():
    """The coordinate is the finding; only the drawn shape is reduced."""
    pts = _blob((90.4123, 23.8456), 0.003, 500, 21)

    cluster = fit_change_clusters(pts[:, 0], pts[:, 1])[0]

    assert cluster["centerLon"] == pytest.approx(90.4123, abs=0.001)
    assert cluster["centerLat"] == pytest.approx(23.8456, abs=0.001)


def test_the_centre_is_reported_in_google_maps_order():
    """lat,lng — the opposite of the lon,lat used everywhere else here."""
    pts = _blob((90.40, 23.80), 0.002, 400, 22)

    cluster = fit_change_clusters(pts[:, 0], pts[:, 1])[0]

    lat_text, lon_text = cluster["coordinate"].split(", ")
    assert float(lat_text) == pytest.approx(cluster["centerLat"], abs=1e-6)
    assert float(lon_text) == pytest.approx(cluster["centerLon"], abs=1e-6)
    # Latitude first: a swap here puts the pin off the coast of Somalia.
    assert 23.0 < float(lat_text) < 24.0
    assert 90.0 < float(lon_text) < 91.0


def test_the_maps_link_points_at_that_coordinate():
    pts = _blob((90.40, 23.80), 0.002, 400, 23)

    cluster = fit_change_clusters(pts[:, 0], pts[:, 1])[0]

    url = cluster["googleMapsUrl"]
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    query = url.split("query=")[1]
    lat_text, lon_text = query.split("%2C")
    assert float(lat_text) == pytest.approx(cluster["centerLat"], abs=1e-6)
    assert float(lon_text) == pytest.approx(cluster["centerLon"], abs=1e-6)
