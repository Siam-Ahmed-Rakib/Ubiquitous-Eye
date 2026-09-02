# Redeploy runbook

How to ship the current commit to the live Azure backend.

**This ran successfully on 2026-09-02**, putting `8dba210` ("cloud composite fix")
live as image tag `7c3c540`. The whole path — build, `az acr login`, `docker push`,
`az containerapp update`, revision swap — is now exercised, not just theorised.

Work top to bottom. Each step says what you should see.

---

## Step 0 — on Windows: push

```bash
git push origin master
```

Confirm what went up, and note the short SHA — it becomes the image tag, so it is
also what you roll back to next time:

```bash
git log --oneline -3
```

Now switch to Ubuntu.

---

## Step 1 — on Ubuntu: check the tree *before* pulling

```bash
cd ~/Ubiquitous-Eye        # or wherever the clone lives
git status --short
```

**You want this to print nothing.** Two things can show up, and both are handled:

**a) Modified tracked files** — most likely `server/Dockerfile` or
`deploy/azure-deploy.sh`, left over from the hand-edits made during the first deploy.
Those edits are already upstream (they were committed as `5799969`), so discarding
the local copies loses nothing:

```bash
git checkout -- server/Dockerfile deploy/azure-deploy.sh
```

**b) Untracked `mobile/web/` or `mobile/pubspec.lock`** — these are now *tracked*
files, so if untracked copies exist locally, `git pull` refuses with
`error: The following untracked working tree files would be overwritten by merge`.
Delete them; the pull brings back the correct versions:

```bash
rm -rf mobile/web mobile/pubspec.lock
```

---

## Step 2 — pull, and confirm the two files arrived

```bash
git pull
git log --oneline -1          # should match what you pushed in Step 0
ls -l mobile/pubspec.lock mobile/web/index.html
```

Both files **must** exist. They are the whole reason this build can work from a
clone: `8dba210` made `server/Dockerfile` COPY the lockfile and stop running
`flutter create . --platforms web`, while `mobile/.gitignore` still excluded both.
The script checks for them anyway and refuses to start without them.

---

## Step 3 — check the machine, before spending 30 minutes on a build

### Disk — the most likely thing to bite

This build is not small. The Flutter builder base image is several GB, the Python
wheels are ~2 GB, and the finished image is ~1.5 GB. **Budget ~15 GB free** where
Docker keeps its data:

```bash
df -h /var/lib/docker
docker system df
```

If you are tight, reclaim space. The previous image lives in Azure Container
Registry, not locally, so pruning locally does **not** affect your ability to roll
back:

```bash
docker system prune -af      # unused images, containers, networks
docker builder prune -af     # build cache
```

Both remove other projects' unused images too. Everything they remove is
re-pullable or re-buildable.

### Docker

```bash
docker info >/dev/null && echo "docker ok"
```

If it says permission denied rather than "daemon not running", that is the problem
you hit last time. `newgrp` only affects the shell you run it in:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

### Azure

```bash
az account show -o table
```

Expect `Azure for Students / Enabled / 2105158@ugrad.cse.buet.ac.bd`. If it fails:

```bash
az login
# if that lands in a tenant with no subscriptions:
az login --tenant 10d93f4f-3089-4c95-8cea-c56f9dea2aa7
```

You do **not** need `.env` for a redeploy. The three secrets already live on the
Container App and are not touched.

---

## Step 4 — run it

```bash
bash deploy/azure-redeploy.sh
```

What it does, in order, and roughly how long:

| stage | what to expect |
|---|---|
| `preflight` | instant. Both paths print `present`. |
| `checking login` | your subscription table. |
| `building ubiquitous-eye:<sha>` | **the long part.** 5 minutes of CPU, plus however long it takes to download a multi-GB Flutter base image and ~2 GB of Python wheels. On a slow link budget 20-40 minutes. It is nearly all network. |
| `pushing to ubiquitouseye29146.azurecr.io` | a few minutes. |
| `rolling a new revision` | ~1 minute. |
| `waiting for the new revision to serve` | polls `/api/health` for up to ~7 minutes. |
| `verifying the deployed bundle is the new build` | should print `new client confirmed live`. |
| `verifying the app still has its secrets` | should print `database reachable, cached classify served`. |

Nothing here creates a resource group, registry or Container Apps environment, so
the Azure for Students region rejections that fought the first deploy
(`southeastasia` and `centralindia` both refused) cannot recur.

---

## Step 5 — check it yourself

The script's own checks are good but do this too. Open the live URL in a browser:

```
https://ubiquitous-eye.redocean-c4117d93.malaysiawest.azurecontainerapps.io
```

- **Browser tab should read "Ubiquitous Eye"**, not `ubiquitous_eye`. That is the
  clearest single sign the new bundle is live, since it comes from the `mobile/web/`
  that was previously missing from the repo.
- Draw a small box, run a **classification** for `2026-02` over
  lon 90.5806-90.6298 / lat 23.8365-23.8815. Should come back in a couple of seconds.
- Run a **change detection** and confirm the new reliability figures appear next to
  the totals.

---

## Step 6 — re-warm the analyze cache

`8dba210` moved `ANALYZE_CACHE_KIND` from `analyze_v5` to `analyze_v7`, so **every
cached change-detection result is now cold**. The old rows are still in the table
under the v5 key; they are simply never read. (Classify is unaffected — its cache
key did not change.)

Warm the three demo areas before showing anyone. This can run unattended:

```bash
U=https://ubiquitous-eye.redocean-c4117d93.malaysiawest.azurecontainerapps.io

warm() {
  echo "warming $1 ..."
  curl -s -X POST "$U/api/sentinel/analyze" \
    -H "Content-Type: application/json" -d "$2" \
    -o /dev/null -w "  HTTP %{http_code}  %{size_download} bytes  %{time_total}s\n" \
    --max-time 1200
}

warm "8x9km 2020-10 -> 2025-10" '{"polygon":[[90.4652,23.9017],[90.5434,23.9017],[90.5434,23.9822],[90.4652,23.9822]],"oldYear":2020,"oldMonth":10,"newYear":2025,"newMonth":10}'
warm "3.8x3.6km 2020-10 -> 2025-10" '{"polygon":[[90.5351,23.8902],[90.5727,23.8902],[90.5727,23.9226],[90.5351,23.9226]],"oldYear":2020,"oldMonth":10,"newYear":2025,"newMonth":10}'
warm "1.8x2.3km 2024-06 -> 2026-08" '{"polygon":[[90.4063,23.7581],[90.4244,23.7581],[90.4244,23.7786],[90.4063,23.7786]],"oldYear":2024,"oldMonth":6,"newYear":2026,"newMonth":8}'
```

Expect each to take **minutes**, and the first one longest — it is the largest area
and the strongest demo. A cold analyze is slower than it used to be: the new code
composites a whole month instead of half, widening up to 60 days when cloud forces
it, and ~98% of that time is fetching satellite scenes one at a time.

Re-run the same three afterwards to confirm they now return in seconds.

---

## If something goes wrong

| symptom | cause and fix |
|---|---|
| Script exits at `preflight` with `MISSING` | The pull did not bring the files. Re-check Step 1b — an untracked copy may have blocked the merge. |
| `permission denied while trying to connect to the Docker daemon` | `sudo usermod -aG docker $USER && newgrp docker` |
| `no space left on device` mid-build | Step 3 disk cleanup, then re-run. The build resumes from cached layers. |
| Build dies during pip with a network error | Just re-run the script. The Dockerfile mounts a BuildKit pip cache, so it resumes from the wheels already fetched rather than starting over. |
| `denied: requested access to the resource is denied` on push | `az acr login --name ubiquitouseye29146` then re-run. |
| Revision never becomes healthy | `az containerapp revision list -n ubiquitous-eye -g ubiquitous-eye-rg -o table`, then `az containerapp logs show -n ubiquitous-eye -g ubiquitous-eye-rg --tail 60` |
| `the new bundle is not being served` | The push succeeded, so this is a revision problem, not a build one. `az containerapp revision list -n ubiquitous-eye -g ubiquitous-eye-rg -o table`, then `--type system` logs for image-pull errors. |
| `classify probe did not succeed` | Secrets may not have carried over. Check the logs; a Sentinel Hub `403 Invalid or expired account` is an account problem, not a code fault. |
| Script stops silently right after `verifying the app still has its secrets`, with no `DONE` block | Fixed on 2026-09-02, but if you ever see it again: it is `set -euo pipefail` killing the script because something closed a pipe on `curl`. It says nothing about the deploy, which by that point has already succeeded. Verify by hand with the two checks in Step 5. |

### Rollback

The previous image is still in the registry:

```bash
az containerapp revision list -n ubiquitous-eye -g ubiquitous-eye-rg -o table
az containerapp update -n ubiquitous-eye -g ubiquitous-eye-rg \
  --image ubiquitouseye29146.azurecr.io/ubiquitous-eye:latest
```

`:latest` is the image the first deploy pushed, i.e. the code as of `5799969`.

---

## What is deliberately not in scope here

- **The APK.** The installed one predates `8dba210`. It keeps working — the client
  tolerates the new `stats` fields being absent, not the reverse — it just will not
  show the reliability figures. Rebuild it later with
  `flutter build apk --release --dart-define=BACKEND_URL=<live url>`.
- **The oversized analyze payload.** `/api/sentinel/analyze` still returns a large
  per-pixel `changes` list the client does not render. Diagnosed, fix designed, not
  implemented. This is what breaks the APK on mobile data.
- **Credential rotation.** The Sentinel Hub secret and the Supabase password were
  both pasted into a chat transcript and have not been rotated.
