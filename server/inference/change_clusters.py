"""Turn scattered changed pixels into a handful of reported regions.

A change map made of individual 30 m cells is honest but close to unreadable:
thousands of specks, no sense of where the change actually *is*, and nothing a
report can name. This groups them and describes each group as an ellipse — a
place, with a centre you can put in a table and hand to someone.

The pipeline is deliberately conservative, because every step here can invent
structure that the pixels do not support:

1. **Drop the strays.** A point far from its neighbours is usually a lone
   misclassified cell, and it drags a cluster centre and inflates an ellipse out
   of all proportion. Distance to the k-th nearest neighbour is the test.
2. **Choose k by silhouette**, not by a fixed number. Asking for four clusters
   when the change is one contiguous clearing produces four arbitrary quarters
   of it.
3. **Fit the ellipse from the covariance** of each cluster, so its shape and
   orientation come from the pixels rather than being a circle drawn around a
   centroid. A river losing water is long and thin, and it should look it.

Everything is reported in degrees, which is the coordinate system the rasters
and the client both work in.
"""

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)

# Below this a "cluster" is a rounding error, not a region worth naming.
MIN_CLUSTER_POINTS = 12

# Never look for more than this many regions. Past it the silhouette differences
# stop being meaningful and the map stops being readable.
MAX_CLUSTERS = 8

# Neighbours used for the outlier test, and the quantile above which a point is
# considered a stray.
OUTLIER_NEIGHBOURS = 8
OUTLIER_QUANTILE = 0.97

# The ellipse is drawn at this many standard deviations along each principal
# axis, which covers ~86% of a 2-D Gaussian.
ELLIPSE_SIGMA = 2.0

# ...then shrunk to this proportion of that. At full size the ellipses of a
# large change area overlap and cover most of the scene, which hides the very
# ground the reader is trying to see. Shrinking marks the dense core of each
# region instead of its full extent: the centre stays exactly where it was, so
# the reported coordinate is unaffected, and the picture stays legible.
ELLIPSE_SHRINK = 0.55

# Silhouette is O(n^2); above this many points it is scored on a sample.
SILHOUETTE_SAMPLE = 4000

# How good a split has to be before it is taken.
#
# Calibrated on real change data, not on synthetic blobs — the difference
# matters. Well-separated synthetic Gaussians score 0.98, but real change is
# usually one connected mass of cells with no gaps in it, and scores far lower:
# measured over the Dhaka fringe, urbanisation peaked at 0.413 (k=2), water loss
# at 0.568 (k=3), and a sparse scatter of deforestation at 0.876. A threshold
# picked from synthetic data (0.72) rejected every real split and returned one
# ellipse swallowing 59 000 cells, which is not a location.
#
# So the bar sits just above a single tight patch (~0.34) and below anything
# real. Be honest about what that means: on contiguous change these regions are
# a readable partition of one area, not evidence of separate sites. Genuinely
# separate sites score far higher and come back separated for the right reason.
MIN_SILHOUETTE = 0.38


def _drop_outliers(xy: np.ndarray) -> np.ndarray:
    """Indices of the points that are not isolated strays."""
    n = len(xy)
    k = min(OUTLIER_NEIGHBOURS, n - 1)
    if k < 1:
        return np.arange(n)

    from sklearn.neighbors import NearestNeighbors

    neigh = NearestNeighbors(n_neighbors=k + 1).fit(xy)
    distances, _ = neigh.kneighbors(xy)
    # Column 0 is the point itself, at distance zero.
    typical = distances[:, 1:].mean(axis=1)
    cutoff = float(np.quantile(typical, OUTLIER_QUANTILE))
    keep = np.flatnonzero(typical <= cutoff)
    # Never let the filter eat the data: if the spread is so uniform that the
    # quantile removes almost everything, keep it all instead.
    return keep if len(keep) >= MIN_CLUSTER_POINTS else np.arange(n)


def _best_k(xy: np.ndarray, max_k: int, random_state: int) -> int:
    """Pick the number of clusters by silhouette score.

    Returns 1 when no split scores well, which is the answer for a single
    contiguous region and the one a fixed k can never give.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    upper = min(max_k, len(xy) // MIN_CLUSTER_POINTS)
    if upper < 2:
        return 1

    best_k, best_score = 1, -1.0
    for k in range(2, upper + 1):
        labels = KMeans(n_clusters=k, n_init=10, random_state=random_state).fit_predict(xy)
        if len(np.unique(labels)) < 2:
            continue
        score = float(silhouette_score(
            xy, labels,
            sample_size=min(SILHOUETTE_SAMPLE, len(xy)),
            random_state=random_state,
        ))
        if score > best_score:
            best_k, best_score = k, score

    # A weak best split is worse than no split: it carves one region into
    # arbitrary pieces that will differ run to run.
    if best_score < MIN_SILHOUETTE:
        return 1
    return best_k


def _ellipse_from(points: np.ndarray) -> dict | None:
    """Centre, semi-axes and rotation describing `points`."""
    if len(points) < 3:
        return None

    centre = points.mean(axis=0)
    # rowvar=False: each row is a point, each column a coordinate.
    cov = np.cov(points, rowvar=False)
    if not np.all(np.isfinite(cov)):
        return None

    values, vectors = np.linalg.eigh(cov)
    values = np.clip(values, 0.0, None)
    order = np.argsort(values)[::-1]
    values, vectors = values[order], vectors[:, order]

    scale = ELLIPSE_SIGMA * ELLIPSE_SHRINK
    semi_major = scale * math.sqrt(float(values[0]))
    semi_minor = scale * math.sqrt(float(values[1]))
    # A perfectly straight line of pixels has zero width; give it enough to be
    # visible rather than rendering nothing.
    semi_minor = max(semi_minor, semi_major * 0.08)
    angle = math.degrees(math.atan2(float(vectors[1, 0]), float(vectors[0, 0])))

    lon, lat = float(centre[0]), float(centre[1])
    return {
        "centerLon": lon,
        "centerLat": lat,
        # Google Maps order is lat,lng — the opposite of the lon,lat used
        # everywhere else here, which is exactly the mistake that drops a pin in
        # the wrong hemisphere. Formatted once, at the source.
        "coordinate": f"{lat:.6f}, {lon:.6f}",
        "googleMapsUrl":
            f"https://www.google.com/maps/search/?api=1&query={lat:.6f}%2C{lon:.6f}",
        "semiMajorDeg": semi_major,
        "semiMinorDeg": semi_minor,
        "angleDeg": angle,
        "points": int(len(points)),
    }


def fit_change_clusters(
    lons,
    lats,
    *,
    max_clusters: int = MAX_CLUSTERS,
    random_state: int = 42,
) -> list[dict]:
    """Group changed pixels into regions, each described as an ellipse.

    Returns a list ordered largest-first, each entry carrying the centre in
    degrees, the ellipse geometry, and how many pixels it covers. An empty list
    means the change was too sparse to describe as regions — which is a real
    answer, not a failure.
    """
    xy = np.column_stack([
        np.asarray(lons, dtype=np.float64),
        np.asarray(lats, dtype=np.float64),
    ])
    xy = xy[np.isfinite(xy).all(axis=1)]
    if len(xy) < MIN_CLUSTER_POINTS:
        return []

    kept = _drop_outliers(xy)
    dropped = len(xy) - len(kept)
    xy = xy[kept]

    k = _best_k(xy, max_clusters, random_state)

    if k == 1:
        labels = np.zeros(len(xy), dtype=int)
    else:
        from sklearn.cluster import KMeans
        labels = KMeans(
            n_clusters=k, n_init=10, random_state=random_state,
        ).fit_predict(xy)

    clusters = []
    for label in range(labels.max() + 1):
        members = xy[labels == label]
        if len(members) < MIN_CLUSTER_POINTS:
            continue
        ellipse = _ellipse_from(members)
        if ellipse is not None:
            clusters.append(ellipse)

    clusters.sort(key=lambda c: c["points"], reverse=True)
    logger.info(
        "  clustered %d points into %d region(s); %d outlier(s) dropped",
        len(xy) + dropped, len(clusters), dropped,
    )
    return clusters


def ellipse_polygon(cluster: dict, steps: int = 72) -> np.ndarray:
    """The cluster's ellipse as an (N, 2) ring of lon/lat points.

    Pillow can only draw axis-aligned ellipses, and these are rotated, so they
    are rendered as polygons. At 72 steps the straight edges are well under a
    pixel at any size this is drawn.
    """
    t = np.linspace(0.0, 2.0 * math.pi, steps, endpoint=False)
    a, b = cluster["semiMajorDeg"], cluster["semiMinorDeg"]
    angle = math.radians(cluster["angleDeg"])
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    x = a * np.cos(t)
    y = b * np.sin(t)
    return np.column_stack([
        cluster["centerLon"] + x * cos_a - y * sin_a,
        cluster["centerLat"] + x * sin_a + y * cos_a,
    ])
