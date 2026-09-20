"""The three change phenomena are directional, not absolute differences."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference.inference import get_mask


def _masks(previous, current):
    frame = lambda labels: pd.DataFrame({  # noqa: E731
        "Longitude": range(len(labels)),
        "Latitude": range(len(labels)),
        "classifier": labels,
    })
    return get_mask(frame(previous), frame(current))


@pytest.mark.parametrize(
    "previous, current, expected",
    [
        # Deforestation: tree before, not tree now — whatever replaced it.
        (["Tree"], ["Soil"], True),
        (["Tree"], ["Building"], True),
        (["Tree"], ["Water"], True),
        # Not deforestation: still tree, or tree gained.
        (["Tree"], ["Tree"], False),
        (["Soil"], ["Tree"], False),
        (["Crop"], ["Soil"], False),
    ],
)
def test_deforestation_is_tree_lost_only(previous, current, expected):
    assert bool(_masks(previous, current)["deforestation"][0]) is expected


@pytest.mark.parametrize(
    "previous, current, expected",
    [
        # Urbanisation is a gain, so the direction is the other way round.
        (["Soil"], ["Building"], True),
        (["Tree"], ["Building"], True),
        (["Water"], ["Building"], True),
        # Not urbanisation: already built, or a building lost.
        (["Building"], ["Building"], False),
        (["Building"], ["Soil"], False),
        (["Soil"], ["Soil"], False),
    ],
)
def test_urbanization_is_building_gained_only(previous, current, expected):
    assert bool(_masks(previous, current)["urbanization"][0]) is expected


@pytest.mark.parametrize(
    "previous, current, expected",
    [
        (["Water"], ["Soil"], True),
        (["Water"], ["Building"], True),
        (["Water"], ["Water"], False),
        (["Soil"], ["Water"], False),  # water gained is not water loss
    ],
)
def test_water_loss_is_water_lost_only(previous, current, expected):
    assert bool(_masks(previous, current)["water_loss"][0]) is expected


def test_cleared_and_built_land_belongs_to_both_layers():
    """The case a single mask code cannot express."""
    result = _masks(["Tree"], ["Building"])

    assert bool(result["deforestation"][0]) is True
    assert bool(result["urbanization"][0]) is True
    # The summary code reports the loss, but it is only a summary — the layers
    # are drawn from the booleans, so this pixel appears in both.
    assert result["mask"][0] == 1


def test_an_unchanged_scene_reports_nothing():
    labels = ["Tree", "Water", "Building", "Soil", "Crop"]
    result = _masks(labels, labels)

    assert not result["deforestation"].any()
    assert not result["urbanization"].any()
    assert not result["water_loss"].any()
    assert (result["mask"] == 0).all()
