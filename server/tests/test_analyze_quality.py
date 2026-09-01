"""Integration tests for cloud-quality gating in change detection."""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_server


POLYGON = [[90.0, 23.7], [90.1, 23.7], [90.1, 23.8], [90.0, 23.8]]


def _classified_composite(class_ids, clear_counts) -> pd.DataFrame:
    size = len(class_ids)
    b08 = np.full(size, 0.30)
    b08[np.asarray(class_ids) == 4] = 0.80  # vegetation -> Tree
    return pd.DataFrame(
        {
            "Longitude": np.linspace(90.02, 90.08, size),
            "Latitude": np.full(size, 23.75),
            "B01": np.full(size, 0.10),
            "B02": np.full(size, 0.08),
            "B03": np.full(size, 0.12),
            "B04": np.full(size, 0.10),
            "B08": b08,
            "B11": np.full(size, 0.10),
            "B12": np.full(size, 0.10),
            "ClassID": np.asarray(class_ids, dtype=np.uint8),
            "ClearObservationCount": np.asarray(clear_counts, dtype=np.uint16),
        }
    )


def test_analyze_uses_adaptive_full_months_and_rejects_under_observed_changes(
    monkeypatch,
):
    old = _classified_composite([4, 6], [2, 1])  # Tree, Water
    new = _classified_composite([5, 5], [2, 2])  # Soil, Soil
    calls = []

    class _Cfg:
        sh_client_id = "test-id"
        sh_client_secret = "test-secret"

    def _pipeline(polygon, year, month, client_id, client_secret, **kwargs):
        calls.append(kwargs)
        frame = (old if year == 2024 else new).copy()
        frame.attrs.update(
            {
                "window_start": date(year, month, 1),
                "window_end": date(year, month, 31),
            }
        )
        return frame

    monkeypatch.setattr(api_server, "build_config", lambda: _Cfg())
    monkeypatch.setattr(api_server, "run_composite_pipeline", _pipeline)
    monkeypatch.setattr(
        api_server,
        "_before_after_pngs",
        lambda *args, **kwargs: (None, None, 0, 0, None, None),
    )
    monkeypatch.setattr(api_server, "_class_map_png", lambda *args, **kwargs: (None, []))

    response = api_server.app.test_client().post(
        "/api/sentinel/analyze",
        json={
            "polygon": POLYGON,
            "oldYear": 2024,
            "oldMonth": 1,
            "newYear": 2025,
            "newMonth": 1,
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert calls == [
        {
            "whole_month": True,
            "min_clear_observations": 2,
            "max_window_days": 60,
        },
        {
            "whole_month": True,
            "min_clear_observations": 2,
            "max_window_days": 60,
        },
    ]

    body = response.get_json()
    assert body["changes"] == [{"Latitude": 23.75, "Longitude": 90.02, "mask": 1}]
    assert body["stats"]["deforestation"] == 1
    assert body["stats"]["waterLoss"] == 0
    assert body["stats"]["eligiblePixels"] == 1
    assert body["stats"]["uncertainPixels"] == 1
    assert body["stats"]["minimumClearObservations"] == 2
