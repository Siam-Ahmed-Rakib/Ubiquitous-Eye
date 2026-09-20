"""Soil-vs-building sub-classifier for Sentinel-2 SCL "Bare Soil" pixels.

SCL class 5 lumps bare ground and built-up surfaces into one label, so
``expand_class`` has to split it a second time. That split used to be the single
threshold ``NDBI > 0.0``; on the ground-truth test set that rule scores
MCC -0.283 — worse than a coin flip — because built-up reflectance in this
region does not separate on NDBI alone. This module replaces it with the
XGBoost model trained by ``train_building_soil/new_training_soil_building.ipynb``
(test F1 0.966, MCC 0.943 on a 1 km spatially-blocked split).

Two things about the feature vector are load-bearing:

* It is rebuilt here from the **raw bands**, never read off the caller's frame.
  ``ensemble_predict`` puts its own ``BSI`` column on the dataframe, and that
  column is ``(B03 + B08) / (B03 - B08)`` — not the Bare Soil Index, and not what
  this model was trained on. The model's normalised version is ``BSI_N``, built
  below, so the two can never be confused.
* Only the seven bands ``EVALSCRIPT_S2`` downloads are used, because they are the
  only ones the composite has. See ``metadata.json`` for the full contract.

If the artifacts are missing the module degrades to the old NDBI rule rather
than failing the request, so the container still serves without the model.
"""

import json
import logging
import os

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soil_building_v1")

SOIL_LABEL = "Soil"
BUILDING_LABEL = "Building"

# The seven bands EVALSCRIPT_S2 returns, as reflectance in 0..1 (raw DN / 10000).
SOURCE_BANDS = ["B01", "B02", "B03", "B04", "B08", "B11", "B12"]

# Feature order the model was trained on. Asserted against metadata.json on load
# rather than trusted, so a mismatched artifact fails loudly instead of silently
# scoring a permuted vector.
FEATURES = [
    "B01", "B02", "B03", "B04", "B08", "B11", "B12",
    "NDVI", "EVI", "NDBI", "MNDWI", "NDWI", "SAVI", "BSI_N", "BI", "AWEI",
    "UI", "IBI", "R_B11_B12",
]

# IBI and B11/B12 both divide by a quantity that crosses zero, so they are
# unbounded (IBI reaches ~7e5 on synthetic reflectance). Training clipped them;
# inference has to clip identically or the model sees values it never saw.
CLIP_BOUNDS = {"IBI": (-10.0, 10.0), "R_B11_B12": (0.0, 10.0)}

_ARTIFACTS = None
_LOAD_FAILED = False


def _safe_div(num, den):
    """Element-wise divide using the guard the rest of the pipeline uses.

    ``inference.py`` writes ``np.where(denom != 0, a / denom, 0.0)``. The inner
    ``np.where`` only stops numpy evaluating the division at the zeros (which
    would warn); the returned values are identical.
    """
    num = np.asarray(num, dtype=np.float64)
    den = np.asarray(den, dtype=np.float64)
    return np.where(den != 0, num / np.where(den != 0, den, 1.0), 0.0)


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build the 19-feature matrix from the seven downloaded bands.

    Columns already on `frame` are ignored — everything is recomputed — so it
    does not matter whether the caller has run `ensemble_predict` first.
    """
    missing = [b for b in SOURCE_BANDS if b not in frame.columns]
    if missing:
        raise KeyError(f"missing required band columns: {missing}")

    b01, b02, b03, b04, b08, b11, b12 = (
        frame[b].to_numpy(dtype=np.float64) for b in SOURCE_BANDS)

    out = pd.DataFrame(index=frame.index)
    for name, col in zip(SOURCE_BANDS, (b01, b02, b03, b04, b08, b11, b12)):
        out[name] = col

    # The four the pipeline already computes, reproduced bit-for-bit.
    out["NDVI"] = _safe_div(b08 - b04, b08 + b04)
    out["EVI"] = _safe_div(2.5 * (b08 - b04), b08 + 6.0 * b04 - 7.5 * b02 + 1.0)
    out["NDBI"] = _safe_div(b11 - b08, b11 + b08)
    out["MNDWI"] = _safe_div(b03 - b11, b03 + b11)

    out["NDWI"] = _safe_div(b03 - b08, b03 + b08)
    out["SAVI"] = _safe_div(1.5 * (b08 - b04), b08 + b04 + 0.5)
    out["BSI_N"] = _safe_div((b11 + b04) - (b08 + b02), (b11 + b04) + (b08 + b02))
    out["BI"] = np.sqrt((b04 ** 2 + b03 ** 2) / 2.0)
    out["AWEI"] = 4.0 * (b03 - b11) - (0.25 * b08 + 2.75 * b12)
    out["UI"] = _safe_div(b12 - b08, b12 + b08)

    mid = (out["SAVI"].to_numpy() + out["MNDWI"].to_numpy()) / 2.0
    ndbi = out["NDBI"].to_numpy()
    out["IBI"] = _safe_div(ndbi - mid, ndbi + mid)

    out["R_B11_B12"] = _safe_div(b11, b12)

    for name, (low, high) in CLIP_BOUNDS.items():
        out[name] = np.clip(out[name].to_numpy(), low, high)

    return out[FEATURES].astype(np.float32)


def _load_artifacts():
    """Load the booster, scaler and metadata once, then reuse them.

    Returns None — permanently, after the first attempt — if the model is not
    installed, so callers fall back to the threshold rule instead of raising on
    every tile.
    """
    global _ARTIFACTS, _LOAD_FAILED
    if _ARTIFACTS is not None or _LOAD_FAILED:
        return _ARTIFACTS

    try:
        import joblib
        import xgboost as xgb

        with open(os.path.join(_MODEL_DIR, "metadata.json"), encoding="utf-8") as handle:
            meta = json.load(handle)

        if list(meta.get("features", [])) != FEATURES:
            raise ValueError(
                "feature order in metadata.json does not match this module: "
                f"{meta.get('features')}")

        booster = xgb.Booster()
        booster.load_model(os.path.join(_MODEL_DIR, "best_model.json"))

        scaler = None
        if meta.get("uses_scaler"):
            scaler = joblib.load(os.path.join(_MODEL_DIR, "scaler.joblib"))

        _ARTIFACTS = {
            "meta": meta,
            "booster": booster,
            "scaler": scaler,
            "threshold": float(meta.get("threshold", 0.5)),
        }
        logger.info(
            "soil/building sub-classifier loaded (%s, threshold %.3f, %d features)",
            meta.get("model_version", "?"), _ARTIFACTS["threshold"], len(FEATURES))
    except Exception as exc:  # pylint: disable=broad-except
        _LOAD_FAILED = True
        logger.warning(
            "soil/building sub-classifier unavailable (%s: %s) — falling back to the "
            "NDBI > 0 rule", type(exc).__name__, exc)
    return _ARTIFACTS


def is_available() -> bool:
    """True when the trained model is installed and loadable."""
    return _load_artifacts() is not None


def predict_soil_proba(frame: pd.DataFrame) -> np.ndarray:
    """P(Soil) per row. `frame` must carry the seven SOURCE_BANDS."""
    import xgboost as xgb

    artifacts = _load_artifacts()
    if artifacts is None:
        raise RuntimeError("soil/building model is not installed")

    features = build_features(frame)
    if artifacts["scaler"] is not None:
        features = pd.DataFrame(
            artifacts["scaler"].transform(features),
            columns=FEATURES, index=features.index)
    matrix = xgb.DMatrix(features.astype(np.float32), feature_names=FEATURES)
    return artifacts["booster"].predict(matrix)


def classify_soil_rows(frame: pd.DataFrame) -> np.ndarray:
    """"Soil" / "Building" for each row. Pass only the ClassID == 5 rows.

    Falls back to the old ``NDBI > 0`` rule if the model is not installed, so a
    deployment without the artifacts still answers.
    """
    if len(frame) == 0:
        return np.empty(0, dtype=object)

    artifacts = _load_artifacts()
    if artifacts is None:
        b08 = frame["B08"].to_numpy(dtype=np.float64)
        b11 = frame["B11"].to_numpy(dtype=np.float64)
        ndbi = _safe_div(b11 - b08, b11 + b08)
        return np.where(ndbi > 0.0, BUILDING_LABEL, SOIL_LABEL).astype(object)

    proba = predict_soil_proba(frame)
    return np.where(
        proba >= artifacts["threshold"], SOIL_LABEL, BUILDING_LABEL).astype(object)
