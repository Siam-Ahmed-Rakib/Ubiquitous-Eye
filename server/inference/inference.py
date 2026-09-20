import logging
import os

import numpy as np
import pandas as pd
import joblib
from scipy.stats import mode

from .soil_building import classify_soil_rows

logger = logging.getLogger(__name__)


_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capstone_model_v2")
_MODEL_NAMES = ["xgboost", "catboost", "lightgbm", "cart"]
_ARTIFACTS = None


def _load_artifacts():
    """Load the scaler, label encoder, and 4 models once, then reuse them.

    ``ensemble_predict`` runs on every classified tile; re-reading ~6 MB of
    joblib files from disk on each call was pure overhead. We load lazily on
    first use so import stays cheap and order-independent.
    """
    global _ARTIFACTS
    if _ARTIFACTS is None:
        _ARTIFACTS = {
            "scaler": joblib.load(os.path.join(_MODEL_DIR, "scaler.joblib")),
            "label_encoder": joblib.load(os.path.join(_MODEL_DIR, "label_encoder.joblib")),
            "models": [
                joblib.load(os.path.join(_MODEL_DIR, f"{name}.joblib"))
                for name in _MODEL_NAMES
            ],
        }
    return _ARTIFACTS


# Thresholds for the water sanity check in `expand_class`. Both must be met for a
# pixel to be taken off the Water class, and they were measured, not guessed —
# see the comment at the water branch for the numbers and the trade-off.
WATER_VEG_NDVI = 0.3
WATER_VEG_MNDWI = 0.0


def _water_check_indices(df):
    """NDVI and MNDWI for the water guard, always rebuilt from the raw bands.

    Not read off `df`: callers hand this function frames in different states —
    `ensemble_predict` has already added its own index columns, the SCL-resolved
    path in api_server adds only NDVI and NDBI — and a guard that silently used
    whichever columns happened to be present would behave differently on the two
    paths. MNDWI is absent on one of them entirely.
    """
    b03 = df["B03"].to_numpy(dtype=np.float64)
    b04 = df["B04"].to_numpy(dtype=np.float64)
    b08 = df["B08"].to_numpy(dtype=np.float64)
    b11 = df["B11"].to_numpy(dtype=np.float64)

    def _div(num, den):
        return np.where(den != 0, num / np.where(den != 0, den, 1.0), 0.0)

    ndvi = pd.Series(_div(b08 - b04, b08 + b04), index=df.index)
    mndwi = pd.Series(_div(b03 - b11, b03 + b11), index=df.index)
    return ndvi, mndwi


def expand_class(df):
    # Vegetation → Tree (NDVI > 0.6) or Crop (NDVI <= 0.6)
    veg_mask = df['ClassID'] == 4
    df.loc[veg_mask & (df['NDVI'] > 0.6), 'classifier'] = 'Tree'
    df.loc[veg_mask & (df['NDVI'] <= 0.6), 'classifier'] = 'Crop'

    # Soil → Building or Soil, decided by the trained sub-classifier.
    #
    # Sentinel-2's "Bare Soil" scene class covers both bare ground and built-up
    # surfaces, so it needs splitting a second time. This used to be
    # `NDBI > 0.0`; that rule scores MCC -0.283 against ground truth — worse
    # than chance — so it is now a model. `classify_soil_rows` rebuilds its own
    # features from the raw bands, which matters: the `BSI` column
    # `ensemble_predict` may already have put on `df` is a different quantity
    # from the one this model was trained on.
    soil_mask = df['ClassID'] == 5
    if soil_mask.any():
        df.loc[soil_mask, 'classifier'] = classify_soil_rows(df.loc[soil_mask])

    # Water → Water, unless the pixel is plainly vegetation.
    #
    # SCL's water class is unreliable over vegetated ground. Measured at the
    # Dhaka fringe (Purbachal, Jan 2026) every one of the 2 213 cells SCL called
    # water had median NDVI +0.362, MNDWI -0.371 and NIR 0.189 — that is a leaf
    # canopy, not a water surface, and it painted the vegetation patches blue.
    #
    # The guard has to be narrow, because Dhaka's real water does NOT look like
    # textbook water: the Buriganga, Turag and Hatirjheel are covered in water
    # hyacinth and algae, so their median NDVI is +0.040 (p95 +0.412). Rejecting
    # on `NDVI < 0` — the obvious rule, and the right one on clear water such as
    # the Sundarbans (NDVI -0.258) — throws away 78.5% of real Dhaka water.
    #
    # Requiring BOTH strong vegetation (NDVI > 0.3) AND a non-water moisture
    # signature (MNDWI < 0) is what separates them: it rejects 88.6% of the
    # forest-as-water errors while costing 11.3% of Dhaka's SCL-water pixels,
    # and those are hyacinth mats, which are arguably vegetation anyway.
    #
    # A rejected pixel is not discarded — it falls through to the vegetation
    # rule, the same NDVI > 0.6 split used for SCL class 4.
    water_mask = df['ClassID'] == 6
    if water_mask.any():
        ndvi, mndwi = _water_check_indices(df)
        looks_vegetated = (ndvi > WATER_VEG_NDVI) & (mndwi < WATER_VEG_MNDWI)
        real_water = water_mask & ~looks_vegetated
        misread = water_mask & looks_vegetated

        df.loc[real_water, 'classifier'] = 'Water'
        df.loc[misread & (ndvi > 0.6), 'classifier'] = 'Tree'
        df.loc[misread & (ndvi <= 0.6), 'classifier'] = 'Crop'

        if misread.any():
            logger.info(
                "water guard: %d of %d SCL-water cells were vegetation (NDVI > %.2f "
                "and MNDWI < %.2f); reclassified as Tree/Crop",
                int(misread.sum()), int(water_mask.sum()),
                WATER_VEG_NDVI, WATER_VEG_MNDWI)

    return df[["Longitude", "Latitude", "classifier"]]

def ensemble_predict(df: pd.DataFrame) -> pd.DataFrame:
    """
    Predict ClassID using an ensemble of 4 models (XGBoost, CatBoost, LightGBM, CART)
    via hard majority voting.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain band columns: B02, B03, B04, B08, B01, B11, B12.
        Spectral indices (EVI, NDBI, MNDWI, BSI, NDVI) are computed if missing.

    Returns
    -------
    pd.DataFrame
        The input dataframe with a 'ClassID' column appended.
    """
    df = df.copy()

    # --- Compute spectral indices if missing ---
    B02, B03, B04, B08, B11 = df["B02"], df["B03"], df["B04"], df["B08"], df["B11"]

    if "EVI" not in df.columns:
        denom = B08 + 6.0 * B04 - 7.5 * B02 + 1.0
        df["EVI"] = np.where(denom != 0, 2.5 * (B08 - B04) / denom, 0.0)

    if "NDBI" not in df.columns:
        denom = B11 + B08
        df["NDBI"] = np.where(denom != 0, (B11 - B08) / denom, 0.0)

    if "MNDWI" not in df.columns:
        denom = B03 + B11
        df["MNDWI"] = np.where(denom != 0, (B03 - B11) / denom, 0.0)

    if "BSI" not in df.columns:
        denom = B03 - B08
        df["BSI"] = np.where(denom != 0, (B03 + B08) / denom, 0.0)

    if "NDVI" not in df.columns:
        denom = B08 + B04
        df["NDVI"] = np.where(denom != 0, (B08 - B04) / denom, 0.0)

    # --- Prepare features ---
    feature_cols = ["B04", "B03", "B08", "B02", "B01", "B12", "B11",
                    "EVI", "NDBI", "MNDWI", "BSI", "NDVI"]
    X = df[feature_cols].values

    # --- Load artifacts (cached across calls) ---
    artifacts = _load_artifacts()
    X_scaled = artifacts["scaler"].transform(X)

    # --- Predict with each model ---
    preds_list = [
        np.asarray(model.predict(X_scaled)).flatten()
        for model in artifacts["models"]
    ]
    predictions = np.vstack(preds_list)  # shape: (4, n_samples)

    # --- Majority vote ---
    ensemble_pred = mode(predictions, axis=0, keepdims=False).mode

    # --- Decode back to original ClassID ---
    df["ClassID"] = artifacts["label_encoder"].inverse_transform(ensemble_pred.astype(int))

    df = expand_class(df)

    return df


def get_mask(prev_df: pd.DataFrame, curr_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare two time-step DataFrames (from ensemble_predict) and produce a change mask.

    Every test is **directional**, never an absolute difference: each one asks
    whether a specific class was there before and is not now (or, for
    urbanisation, was not there before and is now). A pixel that gains tree
    cover is not deforestation, and one that loses a building is not
    urbanisation.

    Returns one boolean column per phenomenon, plus a single ``mask`` code for
    the point list:

        1 — Tree present before, absent now          (deforestation)
        2 — Water present before, absent now         (surface-water loss)
        3 — Building absent before, present now      (urbanisation)
        0 — none of the above

    The booleans are the real answer and the code is a lossy summary of them.
    Cleared woodland that is built over is *both* deforestation and
    urbanisation, and it has to appear in both layers; one integer per pixel
    cannot say that, so the rasters select on the booleans and only the point
    list uses the code. Where a pixel qualifies for more than one, the code
    reports the loss, because a loss is the headline finding.
    """
    prev = prev_df["classifier"]
    curr = curr_df["classifier"]

    deforestation = prev.eq("Tree") & ~curr.eq("Tree")
    water_loss = prev.eq("Water") & ~curr.eq("Water")
    urbanization = ~prev.eq("Building") & curr.eq("Building")

    mask = np.zeros(len(prev_df), dtype=int)
    mask[urbanization.to_numpy()] = 3
    mask[water_loss.to_numpy()] = 2
    mask[deforestation.to_numpy()] = 1

    result = prev_df[["Longitude", "Latitude"]].copy()
    result["mask"] = mask
    result["deforestation"] = deforestation.to_numpy()
    result["water_loss"] = water_loss.to_numpy()
    result["urbanization"] = urbanization.to_numpy()
    return result
