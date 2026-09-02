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
docker info >/dev/null 2>&1 || { echo "ERROR: docker daemon is not running"; exit 1; }

say "checking login"
az account show --query "{sub:name, state:state, user:user.name}" -o table

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
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$FQDN/api/health" || true)
  [ "$code" = "200" ] && break
  sleep 10
done

say "verifying the deployed bundle is the new build"
# Cheapest honest check that the UI actually changed: a string that exists only
# in the new client. A 200 from /api/health proves a container is up, not that
# it is *this* container.
if curl -s --max-time 60 "https://$FQDN/main.dart.js" | grep -q "clear observations"; then
  echo "  new client confirmed live"
else
  echo "  WARNING: new marker string absent -- the old bundle may still be served"
fi

say "DONE"
echo "  LIVE URL:  https://$FQDN"
echo "  image:     $ACR.azurecr.io/$REPO:$TAG"
echo
echo "  Rollback:  az containerapp update -n $APP -g $RG --image $ACR.azurecr.io/$REPO:<previous-sha>"
echo "  Revisions: az containerapp revision list -n $APP -g $RG -o table"
echo "  APK:       flutter build apk --release --dart-define=BACKEND_URL=https://$FQDN"
