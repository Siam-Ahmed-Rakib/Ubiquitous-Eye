"""Tests for full-month clear-observation gating and adaptive expansion."""

import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bimonthly_composite as composite


def test_adaptive_windows_start_with_full_month_and_cap_at_60_days():
    start, end = composite.month_range(2024, 2)

    windows = composite.adaptive_windows(start, end)

    assert [((window_end - window_start).days + 1) for window_start, window_end in windows] == [
        29, 44, 60,
    ]
    assert windows[0] == (date(2024, 2, 1), date(2024, 2, 29))
    assert windows[-1] == (date(2024, 1, 17), date(2024, 3, 16))


def test_adaptive_windows_shift_future_days_back_for_a_current_month():
    windows = composite.adaptive_windows(
        date(2026, 9, 1),
        date(2026, 9, 2),
        available_through=date(2026, 9, 2),
    )

    assert windows[0] == (date(2026, 9, 1), date(2026, 9, 2))
    assert windows[-1] == (date(2026, 7, 5), date(2026, 9, 2))
    assert (windows[-1][1] - windows[-1][0]).days + 1 == 60


def test_adaptive_collection_expands_only_until_every_pixel_has_two_clear_looks(
    monkeypatch,
):
    available_s2 = ["2024-02-25", "2024-03-05", "2024-03-20"]
    fetched = []

    def _search(collection, bbox, start, end, config):
        if collection == composite.DataCollection.SENTINEL2_L2A:
            return available_s2
        return []

    def _fetch_s2(date_string, bbox, size, config):
        fetched.append(date_string)
        bands = np.ones((1, 2, 7), dtype=np.float32)
        if date_string == "2024-03-05":
            invalid = np.array([[False, False]])
        elif date_string == "2024-03-20":
            invalid = np.array([[False, True]])
        else:  # The expanded-window observation fills only the second pixel.
            invalid = np.array([[True, False]])
        scl = np.where(invalid, 8, 4).astype(np.uint8)
        return bands, invalid, scl

    monkeypatch.setattr(composite, "search_dates", _search)
    monkeypatch.setattr(composite, "fetch_s2", _fetch_s2)

    band_stack, _, clear_counts, used_start, used_end = composite.collect_adaptive(
        date(2024, 3, 1),
        date(2024, 3, 31),
        bbox=object(),
        aoi_size=(2, 1),
        s2_cfg=object(),
        ls_catalog_cfg=object(),
        ls_cfg=object(),
        min_clear_observations=2,
        max_window_days=60,
    )

    assert len(band_stack) == 3
    assert clear_counts.tolist() == [[2, 2]]
    assert (used_start, used_end) == (date(2024, 2, 23), date(2024, 4, 8))
    assert fetched == ["2024-03-05", "2024-03-20", "2024-02-25"]
