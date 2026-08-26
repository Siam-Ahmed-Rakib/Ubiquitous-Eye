---
name: sentinel-hub-account-expired
description: "Sentinel Hub credentials: the 2026-08 outage, how it was diagnosed, and that it was resolved by renewal on 2026-08-26"
metadata:
  type: project
---

**Current state (2026-08-26): working.** The user replaced `SH_CLIENT_ID`/`SH_CLIENT_SECRET`
in `.env` (rewritten 15:24 that day). Verified live: an uncached Chittagong classify returned
HTTP 200 with 31,659 classified cells. Live fetches are no longer blocked.

**The outage, kept because the diagnosis is reusable:** from 2026-08-12 every live fetch failed
with `403 {"code": 403, "description": "Invalid or expired account."}` at `catalog.search`
(`bimonthly_composite.py:233`), so classify/analyze returned HTTP 500 for anything not already
cached. It was the *account*, not the credentials: `sentinelhub` acquires the OAuth token
*before* the catalog request (`use_session=True`), and no token/401/`invalid_client` error
appeared anywhere in the log. Rotating the secret would not have helped — the subscription had
lapsed and needed renewing. **If a 403 like this returns, check the log for that signature
before treating a 500 as a code regression.**

**Endpoints span two providers** (`bimonthly_composite.py:179-201`): S2 and Landsat *data* go to
`services.sentinel-hub.com` (AWS eu-central-1, Frankfurt; 161 ms TCP RTT from Bangladesh); the
Landsat *catalog* alone goes to `services-uswest2.sentinel-hub.com` (AWS us-west-2, Oregon;
269 ms). Worth knowing when choosing a hosting region.

Related: [[result-cache-architecture]], [[env-secrets-tracked]], [[where-backend-latency-lives]]
