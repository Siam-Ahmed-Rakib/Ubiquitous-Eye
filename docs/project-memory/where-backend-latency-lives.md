---
name: where-backend-latency-lives
description: "Measured breakdown of backend request latency — network fetch dominates, PNG encoding is the only real CPU cost, classification itself is 0.3 s"
metadata:
  type: project
---

Measured 2026-08-26. Two separate findings; keep them apart.

## 1. Wall-clock is dominated by serial Sentinel Hub fetches — and the user accepts this

Cold `/api/sentinel/classify`, 5x5 km Chittagong AOI (173x183 cells), from Bangladesh: **160 s.**
Of that, ~157 s (98 %) is network: S2 catalog 2.0 s, **12 S2 scene fetches 78.8 s**, Landsat
catalog 2.7 s, **4 Landsat fetches 55.7 s** (each is two HTTP requests), true-colour backdrop
17.6 s. Composite+resample+dataframe 0.45 s. **4-model ensemble over 31,659 cells: 0.30 s.**

Container CPU sampled every 0.5 s: peak 51 % (never saturated one core), mean 3 %, under 20 %
for 96 % of the request. RAM peak 266 MB.

`collect_half` (`server/bimonthly_composite.py:361-395`) loops scenes serially with blocking
`req.get_data()`. A ThreadPoolExecutor would be a ~3x win. **The user explicitly declined this
work on 2026-08-26** — "you don't need to do the network thing, that fetching wait is
acceptable". Do not re-propose it unprompted.

## 2. The actual CPU hotspot is PNG encoding, not classification

`_encode_png` (`server/api_server.py`) used `img.save(..., optimize=True)`, Pillow's most
expensive setting — max zlib effort plus a trial of every row filter. It is called from **9
sites**; a single `/analyze` hits ~6 of them.

On flat banded content (the class maps added in commit 2583694, "coloring map converted but
latency increased") `optimize=True` is pathological. Measured on 1500x1500:

| setting | time | b64 size |
|---|---|---|
| `optimize=True` | 5.72 s | 5748 KB |
| default (level 6) | 0.89 s | 5847 KB |
| **`compress_level=3`** | **0.47 s** | **4956 KB** |

12x faster *and* 14 % smaller, decoding to byte-identical pixels. On photographic true-colour
scenes the trade is real, not free (level 3 is 2x faster but 15 % **larger**), so those were
deliberately left on `optimize=True`.

`_class_map_png` runs twice per analyze, so this saved ~10.5 s/request. Fixed 2026-08-26 via
`CLASS_MAP_PNG_COMPRESS_LEVEL = 3` and an opt-in `compress_level` arg on `_encode_png`.

## Consequence for Azure sizing

Core *count* buys nothing — nothing in the pipeline is parallel. Single-core speed matters only
for the ~1 s of post-fix PNG work. Size for RAM headroom on large AOIs.

Related: [[result-cache-architecture]], [[backend-hosting-constraints]]
