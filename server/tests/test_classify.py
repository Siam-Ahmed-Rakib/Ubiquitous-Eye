"""Tests for the land-use classification endpoint.

The Sentinel Hub fetch is stubbed out, but the real model ensemble and the real
rasteriser run, so these cover the parts that are easy to get wrong: recovering
the grid shape from the flattened composite, realigning the two classification
branches back onto their pixels, and painting the right colour per class.
"""

import base64
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_server


# Land cover the SCL layer already resolved (ClassID 4/5/6) is deterministic —
# `expand_class` splits it on NDVI/NDBI. ClassID 7 goes to the model ensemble,
# whose output we don't pin. (3, 3) is left as no-data.
BASE_CLASS_ID = np.array(
    [
        [4, 4, 5, 5, 6],
        [6, 6, 4, 4, 5],
        [7, 7, 7, 7, 7],
        [4, 5, 6, 4, 5],
    ],
    dtype=np.uint8,
)

EXPECTED = {
    (0, 0): "Tree", (0, 1): "Crop", (0, 2): "Soil", (0, 3): "Soil", (0, 4): "Water",
    (1, 0): "Water", (1, 1): "Water", (1, 2): "Tree", (1, 3): "Crop", (1, 4): "Soil",
    (3, 0): "Tree", (3, 1): "Soil", (3, 2): "Water", (3, 4): "Soil",
}
NO_DATA_CELL = (3, 3)

CROP_CELLS = [(0, 1), (1, 3)]
# Bare soil with NDBI > 0 — `expand_class` calls this Building, and the land-use
# endpoint folds it back into Soil.
BUILT_UP_CELLS = [(0, 2), (3, 1)]
SOIL_CELLS = [(0, 3), (1, 4), (3, 4)]

POLYGON = [[90.00, 23.70], [90.10, 23.70], [90.10, 23.80], [90.00, 23.80]]


def _synthetic_composite() -> pd.DataFrame:
    """A DataFrame shaped exactly like `build_dataframe`'s 10 m output."""
    rows30, cols30 = BASE_CLASS_ID.shape

    # Defaults give NDVI 0.82 (Tree) and NDBI -0.33 (Soil).
    b04 = np.full((rows30, cols30), 0.05)
    b08 = np.full((rows30, cols30), 0.50)
    b11 = np.full((rows30, cols30), 0.15)

    for r, c in CROP_CELLS:  # NDVI 0.33 -> Crop
        b04[r, c], b08[r, c] = 0.15, 0.30
    for r, c in BUILT_UP_CELLS:  # NDBI 0.33 -> Building -> merged to Soil
        b08[r, c], b11[r, c] = 0.20, 0.40
    for r, c in SOIL_CELLS:  # NDBI -0.33 -> Soil
        b08[r, c], b11[r, c] = 0.30, 0.15

    b02 = np.full((rows30, cols30), 0.08)
    b02[NO_DATA_CELL] = np.nan  # residual cloud -> must stay transparent

    def to_10m(arr):
        return np.repeat(np.repeat(arr, 3, axis=0), 3, axis=1).ravel()

    rows10, cols10 = rows30 * 3, cols30 * 3
    lons = np.linspace(90.0, 90.1, cols10)
    lats = np.linspace(23.8, 23.7, rows10)  # north -> south, as the pipeline emits
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    return pd.DataFrame(
        {
            "Longitude": lon_grid.ravel(),
            "Latitude": lat_grid.ravel(),
            "B01": to_10m(np.full((rows30, cols30), 0.10)),
            "B02": to_10m(b02),
            "B03": to_10m(np.full((rows30, cols30), 0.12)),
            "B04": to_10m(b04),
            "B08": to_10m(b08),
            "B11": to_10m(b11),
            "B12": to_10m(np.full((rows30, cols30), 0.10)),
            "ClassID": to_10m(BASE_CLASS_ID).astype(np.uint8),
        }
    )


def _stub_true_color_base(polygon, year, month, client_id, client_secret, **kwargs):
    """A synthetic native-resolution backdrop: a 15×12 scene with one cloud hole.

    The red channel ramps left→right so the stretch has range to work with, and
    one pixel is marked invalid to stand in for a masked cloud.
    """
    height, width = 12, 15
    rgb = np.zeros((height, width, 3), dtype=np.float32)
    rgb[..., 0] = np.linspace(0.02, 0.30, width)[None, :]  # B04 (red) ramp
    rgb[..., 1] = 0.10  # B03 (green)
    rgb[..., 2] = 0.08  # B02 (blue)
    valid = np.ones((height, width), dtype=bool)
    valid[0, 0] = False  # a masked cloud / no-data pixel
    return rgb, valid


@pytest.fixture
def stub_pipeline(monkeypatch):
    class _Cfg:
        sh_client_id = "test-id"
        sh_client_secret = "test-secret"

    monkeypatch.setattr(api_server, "build_config", lambda: _Cfg())
    monkeypatch.setattr(
        api_server,
        "run_composite_pipeline",
        lambda polygon, year, month, cid, secret, **kwargs: _synthetic_composite(),
    )
    monkeypatch.setattr(api_server, "fetch_true_color_base", _stub_true_color_base)


def test_grid_shape_recovers_meshgrid_dimensions():
    df = _synthetic_composite()
    assert api_server._grid_shape(df) == (12, 15)


def test_classify_labels_matches_expand_class_rules():
    df = _synthetic_composite()
    # Sample the 30 m pixel centres out of the ×3 expanded grid.
    rows10, cols10 = api_server._grid_shape(df)
    row_idx = np.arange(rows10 // 3) * 3 + 1
    col_idx = np.arange(cols10 // 3) * 3 + 1
    sub = df.iloc[(row_idx[:, None] * cols10 + col_idx[None, :]).ravel()]

    grid = api_server._classify_labels(sub).reshape(4, 5)

    for cell, label in EXPECTED.items():
        assert grid[cell] == label, f"{cell} -> {grid[cell]!r}, wanted {label!r}"

    assert grid[NO_DATA_CELL] == "", "NaN bands must stay unlabelled"

    # Building must never surface: it is merged into Soil before we get here.
    assert "Building" not in grid
    allowed = set(api_server.LAND_COVER_COLORS) | {""}
    for c in range(5):
        assert grid[2, c] in allowed, f"ensemble emitted {grid[2, c]!r}"


def test_classify_endpoint_paints_expected_colours(stub_pipeline):
    client = api_server.app.test_client()
    res = client.post("/api/sentinel/classify", json={"polygon": POLYGON, "year": 2024, "month": 3})

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "success"

    assert body["bounds"] == {"north": 23.8, "south": 23.7, "east": 90.1, "west": 90.0}
    assert body["stats"]["gridWidth"] == 5
    assert body["stats"]["gridHeight"] == 4
    assert body["stats"]["resolutionMeters"] == 30

    img = Image.open(io.BytesIO(base64.b64decode(body["imagePngBase64"])))
    assert img.mode == "RGBA"
    assert (img.width, img.height) == (body["imageWidth"], body["imageHeight"])

    # Nearest-neighbour upscale means each cell is a solid block; sample centres.
    cell_w, cell_h = img.width // 5, img.height // 4
    palette = api_server.LAND_COVER_COLORS

    for (row, col), label in EXPECTED.items():
        px = img.getpixel((col * cell_w + cell_w // 2, row * cell_h + cell_h // 2))
        assert px == (*palette[label], 255), f"cell {(row, col)} painted {px}, wanted {label}"

    row, col = NO_DATA_CELL
    alpha = img.getpixel((col * cell_w + cell_w // 2, row * cell_h + cell_h // 2))[3]
    assert alpha == 0, "no-data cell must be fully transparent"


def test_classify_endpoint_returns_cloud_masked_native_backdrop(stub_pipeline):
    client = api_server.app.test_client()
    body = client.post(
        "/api/sentinel/classify", json={"polygon": POLYGON, "year": 2024, "month": 3}
    ).get_json()

    base = Image.open(io.BytesIO(base64.b64decode(body["baseImagePngBase64"])))
    overlay = Image.open(io.BytesIO(base64.b64decode(body["imagePngBase64"])))

    # The backdrop is the dedicated native-resolution scene (15×12 from the stub),
    # not tied to the 30 m mask size — the client stacks them by filling one box.
    assert base.mode == "RGBA"
    assert base.size == (15, 12)
    assert overlay.size == (body["imageWidth"], body["imageHeight"])

    # ...but both cover the same bounding box, so their aspect ratios agree.
    assert abs(base.width / base.height - overlay.width / overlay.height) < 0.01

    # The masked cloud pixel is transparent; clear imagery is opaque.
    assert base.getpixel((0, 0))[3] == 0
    assert base.getpixel((7, 6))[3] == 255

    # The red ramp survives the stretch: right edge is redder than the left.
    assert base.getpixel((14, 6))[0] > base.getpixel((1, 6))[0]


def test_classify_falls_back_to_composite_backdrop_when_fetch_fails(
    stub_pipeline, monkeypatch
):
    def _boom(*args, **kwargs):
        raise RuntimeError("no network / no credentials")

    monkeypatch.setattr(api_server, "fetch_true_color_base", _boom)

    client = api_server.app.test_client()
    body = client.post(
        "/api/sentinel/classify", json={"polygon": POLYGON, "year": 2024, "month": 3}
    ).get_json()

    assert body["status"] == "success"
    base = Image.open(io.BytesIO(base64.b64decode(body["baseImagePngBase64"])))
    overlay = Image.open(io.BytesIO(base64.b64decode(body["imagePngBase64"])))

    # The fallback renders the 30 m composite, which registers with the mask.
    assert base.size == overlay.size == (body["imageWidth"], body["imageHeight"])

    cell_w, cell_h = base.width // 5, base.height // 4
    row, col = NO_DATA_CELL
    px = base.getpixel((col * cell_w + cell_w // 2, row * cell_h + cell_h // 2))
    assert px[3] == 0, "no-data cell must stay transparent in the fallback too"


def test_true_colour_render_is_transparent_without_any_data():
    df = _synthetic_composite()
    df[["B02", "B03", "B04"]] = np.nan
    img = api_server._render_true_color(df.iloc[:20], 4, 5)

    assert img.size == (5, 4)
    assert img.getextrema()[3] == (0, 0), "alpha must be zero everywhere"


def test_classify_endpoint_reports_class_stats(stub_pipeline):
    client = api_server.app.test_client()
    body = client.post(
        "/api/sentinel/classify", json={"polygon": POLYGON, "year": 2024, "month": 3}
    ).get_json()

    by_name = {c["name"]: c for c in body["classes"]}
    assert set(by_name) == {"Tree", "Crop", "Water", "Soil"}
    assert "Building" not in by_name

    # The 14 SCL-resolved cells are pinned; the 5 ensemble cells may add to any
    # class, and the no-data cell to none.
    assert by_name["Water"]["pixels"] >= 4
    assert by_name["Tree"]["pixels"] >= 3
    assert by_name["Soil"]["pixels"] >= 5  # 3 bare soil + 2 folded-in built-up
    assert by_name["Crop"]["pixels"] >= 2

    assert body["stats"]["totalCells"] == 20
    assert 14 <= body["stats"]["classifiedCells"] <= 19  # no-data cell never counts

    total_pct = sum(c["percent"] for c in body["classes"])
    assert total_pct == pytest.approx(100.0 * body["stats"]["classifiedCells"] / 20, abs=0.05)

    assert body["classes"] == sorted(body["classes"], key=lambda c: -c["pixels"])


def test_classify_endpoint_rejects_bad_input(stub_pipeline):
    client = api_server.app.test_client()

    res = client.post("/api/sentinel/classify", json={"polygon": [[90.0, 23.7]]})
    assert res.status_code == 400

    res = client.post("/api/sentinel/classify", json={"polygon": POLYGON, "month": 13})
    assert res.status_code == 400


def test_classify_composites_the_whole_month(monkeypatch):
    """Land-use classification must ask for every pass in the month so the
    cloud-masked median can actually remove clouds (the planet.py method)."""
    captured = {}

    class _Cfg:
        sh_client_id = "test-id"
        sh_client_secret = "test-secret"

    def _capture(polygon, year, month, cid, secret, **kwargs):
        captured.update(kwargs)
        return _synthetic_composite()

    monkeypatch.setattr(api_server, "build_config", lambda: _Cfg())
    monkeypatch.setattr(api_server, "run_composite_pipeline", _capture)
    monkeypatch.setattr(api_server, "fetch_true_color_base", _stub_true_color_base)

    client = api_server.app.test_client()
    res = client.post(
        "/api/sentinel/classify", json={"polygon": POLYGON, "year": 2024, "month": 3}
    )

    assert res.status_code == 200
    assert captured.get("whole_month") is True


def test_month_range_spans_the_full_month():
    import bimonthly_composite as bc

    start, end = bc.month_range(2024, 2)  # a leap February
    assert (start.month, start.day) == (2, 1)
    assert (end.month, end.day) == (2, 29)
