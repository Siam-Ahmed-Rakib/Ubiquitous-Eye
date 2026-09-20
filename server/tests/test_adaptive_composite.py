"""Tests for full-month clear-observation gating and adaptive expansion."""

import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bimonthly_composite as composite


def test_adaptive_windows_start_at_60_days_and_cap_at_90():
    start, end = composite.month_range(2024, 2)

    windows = composite.adaptive_windows(start, end)

    # The first window is already 60 days: a calendar month alone does not give
    # enough clear passes, so it is widened before any scene is fetched.
    assert [((window_end - window_start).days + 1) for window_start, window_end in windows] == [
        60, 75, 90,
    ]
    assert windows[0] == (date(2024, 1, 17), date(2024, 3, 16))
    assert windows[-1] == (date(2024, 1, 2), date(2024, 3, 31))


def test_adaptive_windows_stay_centred_on_the_requested_month():
    """The extra days are split evenly, so the composite still represents the month."""
    start, end = composite.month_range(2025, 1)

    first_start, first_end = composite.adaptive_windows(start, end)[0]

    assert (first_end - first_start).days + 1 == 60
    assert (start - first_start).days == 14   # days added before January
    assert (first_end - end).days == 15       # days added after January


def test_adaptive_windows_shift_future_days_back_for_a_current_month():
    windows = composite.adaptive_windows(
        date(2026, 9, 1),
        date(2026, 9, 2),
        available_through=date(2026, 9, 2),
    )

    # Nothing can be fetched after `available_through`, so the whole window sits
    # behind it rather than reaching into a future with no acquisitions.
    assert windows[0][1] == date(2026, 9, 2)
    assert (windows[0][1] - windows[0][0]).days + 1 == 60
    assert windows[-1] == (date(2026, 6, 5), date(2026, 9, 2))
    assert (windows[-1][1] - windows[-1][0]).days + 1 == 90


class _FakeConfig:
    """Enough of SHConfig for the collection lookup; the fetches are stubbed."""

    sh_base_url = composite.LEGACY_BASE_URL


def test_adaptive_collection_expands_only_until_every_pixel_has_two_clear_looks(
    monkeypatch,
):
    # 02-10 sits outside the 60-day base window (Feb 16 - Apr 15) and inside the
    # 75-day first expansion, so it is what the gate has to reach for.
    available_s2 = ["2024-02-10", "2024-03-05", "2024-03-20"]
    fetched = []

    # The S2 collection is now bound to whichever deployment the config points
    # at, so compare on api_id rather than on enum identity.
    def _search(collection, bbox, start, end, config, max_cloud_coverage=None):
        if collection.api_id == composite.DataCollection.SENTINEL2_L2A.api_id:
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
        s2_cfg=_FakeConfig(),
        ls_catalog_cfg=_FakeConfig(),
        ls_cfg=_FakeConfig(),
        min_clear_observations=2,
        max_window_days=90,
    )

    assert len(band_stack) == 3
    assert clear_counts.tolist() == [[2, 2]]
    # Stopped at the first expansion (75 days) rather than running on to 90.
    assert (used_start, used_end) == (date(2024, 2, 8), date(2024, 4, 22))
    assert (used_end - used_start).days + 1 == 75
    assert fetched == ["2024-03-05", "2024-03-20", "2024-02-10"]
