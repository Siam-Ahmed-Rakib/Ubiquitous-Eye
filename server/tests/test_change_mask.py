"""Tests for directional forest and surface-water loss detection."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference.inference import get_mask


def _classes(*labels: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Longitude": range(len(labels)),
            "Latitude": range(len(labels)),
            "classifier": labels,
        }
    )


def test_loss_is_previous_presence_minus_current_presence():
    previous = _classes("Tree", "Tree", "Water", "Water", "Soil", "Soil")
    current = _classes("Soil", "Water", "Soil", "Tree", "Tree", "Water")

    result = get_mask(previous, current)

    # Tree/Water losses have delta 1. Tree/Water gains have delta -1 and are
    # deliberately not reported as losses.
    assert result["mask"].tolist() == [1, 1, 2, 2, 0, 0]


def test_unchanged_presence_or_absence_has_zero_delta():
    previous = _classes("Tree", "Water", "Soil", "Building", "Crop")
    current = _classes("Tree", "Water", "Soil", "Building", "Crop")

    result = get_mask(previous, current)

    assert result["mask"].tolist() == [0, 0, 0, 0, 0]
