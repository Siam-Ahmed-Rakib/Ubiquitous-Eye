import os
import numpy as np
import pandas as pd
import joblib
from scipy.stats import mode


def expand_class(df):
    # Vegetation → Tree (NDVI > 0.6) or Crop (NDVI <= 0.6)
    veg_mask = df['ClassID'] == 4
    df.loc[veg_mask & (df['NDVI'] > 0.6), 'classifier'] = 'Tree'
    df.loc[veg_mask & (df['NDVI'] <= 0.6), 'classifier'] = 'Crop'

    # Soil → Building (NDBI > 0.0) or Soil (NDBI <= 0.0)
    soil_mask = df['ClassID'] == 5
    df.loc[soil_mask & (df['NDBI'] > 0.0), 'classifier'] = 'Building'
    df.loc[soil_mask & (df['NDBI'] <= 0.0), 'classifier'] = 'Soil'

    # Water → Water
    water_mask = df['ClassID'] == 6
    df.loc[water_mask, 'classifier'] = 'Water'
    
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

    # --- Load artifacts ---
    model_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capstone_model_v2")

    scaler = joblib.load(os.path.join(model_dir, "scaler.joblib"))
    label_encoder = joblib.load(os.path.join(model_dir, "label_encoder.joblib"))

    X_scaled = scaler.transform(X)

    # --- Load models and predict ---
    model_names = ["xgboost", "catboost", "lightgbm", "cart"]
    predictions = np.array([
        joblib.load(os.path.join(model_dir, f"{name}.joblib")).predict(X_scaled)
        for name in model_names
    ])  # shape: (4, n_samples)

    # --- Majority vote ---
    ensemble_pred = mode(predictions, axis=0, keepdims=False).mode

    # --- Decode back to original ClassID ---
    df["ClassID"] = label_encoder.inverse_transform(ensemble_pred.astype(int))

    df = expand_class(df)

    return df


def get_mask(prev_df: pd.DataFrame, curr_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare two time-step DataFrames (from ensemble_predict) and produce a change mask.

    Mask values:
        1 — Tree was present previously and is absent currently (deforestation)
        2 — Water was present previously and is absent currently (water loss)
        0 — No significant change
    """
    mask = np.zeros(len(prev_df), dtype=int)

    prev = prev_df["classifier"]
    curr = curr_df["classifier"]

    # Subtract the current presence flag from the previous one. A delta of 1
    # means the class was present before and is absent now (loss); 0 means no
    # change and -1 means a gain, neither of which is a loss.
    tree_delta = prev.eq("Tree").astype(np.int8) - curr.eq("Tree").astype(np.int8)
    water_delta = prev.eq("Water").astype(np.int8) - curr.eq("Water").astype(np.int8)

    mask[tree_delta == 1] = 1
    mask[water_delta == 1] = 2

    result = prev_df[["Longitude", "Latitude"]].copy()
    result["mask"] = mask
    return result
