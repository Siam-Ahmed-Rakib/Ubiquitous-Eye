---
name: env-secrets-tracked
description: .env was committed to git; now untracked. Existing SH creds still in history.
metadata: 
  node_type: memory
  type: project
  originSessionId: 241d6d28-6325-4f26-9185-9cf014e2bdd1
---

On 2026-07-22 the repo had **no `.gitignore`** and `.env` (with real Sentinel Hub `SH_CLIENT_ID`/`SH_CLIENT_SECRET`) was **tracked in git**. Added a `.gitignore` (ignores `.env`) and ran `git rm --cached .env` so the file stays on disk but is no longer tracked — this protects the Supabase `DATABASE_URL` the user is adding.

**Still outstanding:** the Sentinel Hub creds remain in git *history*. History was NOT rewritten (would need explicit user go-ahead + credential rotation). If the user ever pushes this repo publicly, flag that they should rotate SH creds and scrub history.

Secrets live only in `.env` (compose auto-loads it for `${VAR}` interpolation) — never echo `.env` contents into the transcript. Verify the cache turned on via the masked log line `Result cache enabled (...)`, not by reading the URL.

Related: [[result-cache-architecture]]
