#!/usr/bin/env bash
# Roll a NEW image onto the already-provisioned Ubiquitous Eye Container App.
#
# Use this, not azure-deploy.sh, for every deploy after the first. azure-deploy.sh
# is a create-only script: it picks a *random* registry name when ACR_NAME is unset
# and calls `az containerapp create`, so re-running it would build a second
# registry and then fail on the app that already exists.
#
#   git pull
#   bash deploy/azure-redeploy.sh
#
# Needs Docker (the image is built locally -- `az acr build` is rejected on this
# subscription with TasksOperationsNotAllowed) and a working `az account show`,
# which means Ubuntu: the BUET tenant's Conditional Access policy blocks Windows.
#
# It does NOT need .env, and does NOT need Flutter on the host. The three secrets
# already live on the Container App and are left untouched; Flutter runs inside
# the image's first build stage. Nothing here creates a resource group, registry
# or environment either, so none of the Azure for Students region policies that
# fought the first deploy (southeastasia and centralindia both rejected for
# Container Apps) can come back.
set -euo pipefail

RG=ubiquitous-eye-rg
APP=ubiquitous-eye
ACR=${ACR_NAME:-ubiquitouseye29146}
REPO=ubiquitous-eye

# Tag by commit, never :latest. Container Apps keys a new revision off the image
# *reference*; pushing a new :latest leaves the reference unchanged, so the app
# can happily keep serving the old layers. A distinct tag also makes rollback a
# one-liner: point --image back at the previous SHA.
SHA=$(git rev-parse --short HEAD)
DIRTY=$(git status --porcelain | head -1)
TAG="$SHA"
[ -n "$DIRTY" ] && TAG="$SHA-dirty"

say() { printf '\n=== %s ===\n' "$1"; }

say "preflight"
# The Dockerfile COPYs mobile/pubspec.lock and no longer runs
# `flutter create . --platforms web`, so stage one needs both of these in the
# build context. Both are listed in mobile/.gitignore, so a fresh clone does not
# have them and the Flutter build dies several minutes in with an error that
# does not name the cause. Check now instead.
missing=0
for f in mobile/pubspec.lock mobile/web/index.html; do
  if [ -e "$f" ]; then
    printf '  %-26s present\n' "$f"
  else
    printf '  %-26s MISSING\n' "$f"
    missing=1
  fi
done
if [ "$missing" = 1 ]; then
  cat <<'MSG'

  server/Dockerfile requires those paths but mobile/.gitignore excludes them,
  so they never arrive in a clone. Either commit them upstream (recommended --
  mobile/web/ is also what gives the browser tab its "Ubiquitous Eye" title),
  or regenerate them locally, which floats the dependency versions the
  digest-pinned Dockerfile was trying to make reproducible:

      cd mobile && flutter create . --platforms web && flutter pub get && cd ..

MSG
  exit 1
fi

command -v docker >/dev/null || { echo "ERROR: docker not found; this script builds locally"; exit 1; }
if ! docker info >/dev/null 2>&1; then
  cat <<'MSG'
  ERROR: cannot talk to the Docker daemon.

  If it is a permission problem rather than a stopped daemon, this is the fix
  that was needed on this machine the first time round -- and `newgrp` only
  affects the shell you run it in, so it has to be re-run or the session
  re-opened:

      sudo usermod -aG docker $USER
      newgrp docker

MSG
  exit 1
fi

say "checking login"
az account show --query "{sub:name, state:state, user:user.name}" -o table
# Cheap and idempotent. `az containerapp` lives in an extension; if it were
# missing the update below would fail with an unhelpful "command not found".
az extension add --name containerapp --upgrade --only-show-errors >/dev/null 2>&1 || true

say "building $REPO:$TAG"
# BuildKit is required: the Dockerfile declares `# syntax=docker/dockerfile:1.7`
# and mounts a pip cache. Modern Docker defaults to it; forced here so an older
# daemon fails loudly rather than choking on the mount syntax.
DOCKER_BUILDKIT=1 docker build -f server/Dockerfile -t "$REPO:$TAG" .

say "pushing to $ACR.azurecr.io"
az acr login --name "$ACR"
docker tag "$REPO:$TAG" "$ACR.azurecr.io/$REPO:$TAG"
docker push "$ACR.azurecr.io/$REPO:$TAG"

say "rolling a new revision"
az containerapp update \
  -n "$APP" -g "$RG" \
  --image "$ACR.azurecr.io/$REPO:$TAG" \
  --only-show-errors -o none

FQDN=$(az containerapp show -n "$APP" -g "$RG" --query "properties.configuration.ingress.fqdn" -o tsv)

say "waiting for the new revision to serve"
# Three traps live in this block. All three fired on the first real redeploy and
# between them reported a completely healthy deploy as two failures:
#
#  1. A 200 from /api/health does NOT prove the swap happened. The OLD revision
#     answers it just as happily, and `az containerapp update` returns when the
#     update is *accepted*, not when the new revision carries traffic. So the
#     marker check has to be the thing that gets retried, not the health check.
#  2. `curl ... | grep -q` looks harmless and is not. grep -q exits on its first
#     match and closes the pipe, curl dies with exit 23 ("failed writing body"),
#     and `set -o pipefail` reports the pipeline as failed -- so a SUCCESSFUL
#     match gets reported as a failure. `curl ... | head -c` is the same bug and
#     worse: it also trips `set -e` and kills the script mid-verification.
#     Nothing below pipes out of curl; every response goes to a file first.
#  3. The classify response is ~900 kB and its `"cached"` key sits at byte
#     ~865,000. Inspecting the first 400 bytes could never have matched it.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

live=0
waited=0
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://$FQDN/api/health" || true)
  if [ "$code" = "200" ] \
     && curl -s --max-time 90 -o "$TMP/main.dart.js" "https://$FQDN/main.dart.js" \
     && grep -q "clear observations" "$TMP/main.dart.js"; then
    live=1
    break
  fi
  waited=$((waited + 10))
  sleep 10
done

say "verifying the deployed bundle is the new build"
if [ "$live" = 1 ]; then
  echo "  new client confirmed live (${waited}s after the update)"
else
  echo "  WARNING: the new bundle is not being served."
  echo "  The push succeeded, so this is a revision problem, not a build problem:"
  echo "      az containerapp revision list -n $APP -g $RG -o table"
  echo "      az containerapp logs show -n $APP -g $RG --type system --tail 40"
fi

say "verifying the app still has its secrets"
# `az containerapp update --image` patches the container template and leaves
# secrets and env-vars alone, so this should always pass -- but "should" is not
# "did", and a lost DATABASE_URL would not show up in /api/health. This area is
# in the classify cache, whose key did NOT change in 8dba210, so a `cached:true`
# answer proves the database secret survived the revision.
if curl -s --max-time 180 -o "$TMP/classify.json" \
     -X POST "https://$FQDN/api/sentinel/classify" \
     -H "Content-Type: application/json" \
     -d '{"polygon":[[90.5805528458285,23.8364742130499],[90.6298069680125,23.8364742130499],[90.6298069680125,23.881519258095],[90.5805528458285,23.881519258095]],"year":2026,"month":2}'
then
  if grep -q '"cached": *true' "$TMP/classify.json"; then
    echo "  database reachable, cached classify served"
  elif grep -q '"status": *"success"' "$TMP/classify.json"; then
    echo "  classify succeeded but was NOT cached -- check the DB, it should have hit"
  else
    echo "  WARNING: classify probe did not succeed. First 200 bytes:"
    echo "    $(head -c 200 "$TMP/classify.json")"
  fi
else
  echo "  WARNING: classify request failed outright -- the app may still be starting."
  echo "  Re-run by hand before assuming the secrets are gone:"
  echo "      curl -s -o /tmp/c.json https://$FQDN/api/health && cat /tmp/c.json"
fi

say "DONE"
echo "  LIVE URL:  https://$FQDN"
echo "  image:     $ACR.azurecr.io/$REPO:$TAG"
echo
echo "  Rollback:  az containerapp update -n $APP -g $RG --image $ACR.azurecr.io/$REPO:<previous-sha>"
echo "  Revisions: az containerapp revision list -n $APP -g $RG -o table"
echo "  APK:       flutter build apk --release --dart-define=BACKEND_URL=https://$FQDN"
