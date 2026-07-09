"""
Convert Sentinel-2 SCL JP2 to CSV.

CSV columns:
- Longitude
- Latitude
- ClassID
- ClassName

By default, the script uses every pixel. For large areas, use --step to sample
every Nth pixel in both directions.
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import xy
from rasterio.errors import NotGeoreferencedWarning


CLASS_DICT = {
    0: "No Data",
    1: "Saturated",
    2: "Dark Area Pixels",
    3: "Cloud Shadow",
    4: "Vegetation",
    5: "Bare Soil",
    6: "Water",
    7: "Unclassified",
    8: "Cloud Medium Probability",
    9: "Cloud High Probability",
    10: "Thin Cirrus",
    11: "Snow / Ice",
}

# Cloud masking values (from planet.py)
CLOUD_SCL_VALUES = [3, 8, 9, 10]  # Cloud Shadow, Medium/High Probability, Thin Cirrus


def is_cloud_pixel(class_id: int) -> bool:
    """Check if a pixel is classified as cloud."""
    return class_id in CLOUD_SCL_VALUES


def find_latest_scl_file(search_root: Path) -> Path:
    files = sorted(search_root.rglob("*_SCL_20m.jp2"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No SCL JP2 files found under: {search_root}")
    return files[0]


def convert_scl_to_csv(scl_path: Path, csv_path: Path, step: int = 1, exclude_clouds: bool = True) -> None:
    if step < 1:
        raise ValueError("step must be >= 1")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
        ds = rasterio.open(scl_path)

    with ds:
        scl = ds.read(1)
        transform = ds.transform
        crs = ds.crs

    has_georef = crs is not None and not transform.is_identity

    row_idx, col_idx = np.indices(scl.shape)
    if step > 1:
        row_idx = row_idx[::step, ::step]
        col_idx = col_idx[::step, ::step]
        scl = scl[::step, ::step]

    class_id = scl.reshape(-1)
    data = {
        "PixelRow": row_idx.reshape(-1),
        "PixelCol": col_idx.reshape(-1),
        "ClassID": class_id,
    }

    if has_georef:
        lon, lat = xy(transform, row_idx, col_idx)
        data["Longitude"] = np.asarray(lon).reshape(-1)
        data["Latitude"] = np.asarray(lat).reshape(-1)

    df = pd.DataFrame(data)
    df["ClassName"] = df["ClassID"].map(CLASS_DICT).fillna("Unknown")

    # Remove cloud pixels if requested
    if exclude_clouds:
        original_count = len(df)
        df = df[~df["ClassID"].isin(CLOUD_SCL_VALUES)]
        removed_count = original_count - len(df)
        print(f"Removed {removed_count} cloud pixels ({100 * removed_count / original_count:.1f}%)")

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert SCL JP2 to CSV")
    parser.add_argument("--scl", type=Path, default=None, help="Path to SCL JP2 file")
    parser.add_argument(
        "--search-root",
        type=Path,
        default=Path(__file__).resolve().parent / "GRANULE",
        help="Root folder used to auto-find latest SCL file when --scl is not set",
    )
    parser.add_argument("--out", type=Path, default=None, help="Output CSV path")
    parser.add_argument("--step", type=int, default=1, help="Sampling step. 1 = all pixels")
    parser.add_argument(
        "--no-clouds",
        action="store_true",
        help="Include cloud pixels (default: exclude clouds)",
    )
    args = parser.parse_args()

    scl_path = args.scl if args.scl else find_latest_scl_file(args.search_root)
    out_path = args.out if args.out else scl_path.with_suffix(".csv")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
        with rasterio.open(scl_path) as ds:
            has_georef = ds.crs is not None and not ds.transform.is_identity

    convert_scl_to_csv(scl_path, out_path, step=args.step, exclude_clouds=not args.no_clouds)

    print(f"SCL source : {scl_path}")
    print(f"CSV output : {out_path}")
    print(f"Step       : {args.step}")
    print(f"GeoCoords  : {'yes' if has_georef else 'no (pixel indices only)'}")
    print(f"Cloud removal : {'disabled' if args.no_clouds else 'enabled'}")


if __name__ == "__main__":
    main()
