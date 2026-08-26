#!/usr/bin/env bash
# Deploy Ubiquitous Eye to Azure Container Apps (Southeast Asia).
#
# Run this from a machine where `az account show` already works. It needs NO
# Docker: `az acr build` uploads only the build context (~30 MB after
# .dockerignore) and builds the image inside Azure.
#
#   git clone https://github.com/Siam-Ahmed-Rakib/Ubiquitous-Eye.git
#   cd Ubiquitous-Eye
#   # create .env with the three secrets (see .env.example), then:
#   bash deploy/azure-deploy.sh
set -euo pipefail

RG=ubiquitous-eye-rg
RG_LOC=southeastasia
LOC=malaysiawest
ENVN=ubiquitous-eye-env
APP=ubiquitous-eye
IMG=ubiquitous-eye:latest
ACR=${ACR_NAME:-ubiquitouseye$RANDOM}

say() { printf '\n=== %s ===\n' "$1"; }

say "checking login"
az account show --query "{sub:name, state:state, user:user.name}" -o table

say "reading secrets from .env (values are never printed)"
[ -f .env ] || { echo "ERROR: .env not found. Copy it from your Windows machine, or create it from .env.example"; exit 1; }
set -a; . ./.env; set +a
for v in SH_CLIENT_ID SH_CLIENT_SECRET DATABASE_URL; do
  [ -n "${!v:-}" ] || { echo "ERROR: $v missing from .env"; exit 1; }
  printf '  %-18s present
' "$v"
done

say "extension + providers"
az extension add --name containerapp --upgrade --only-show-errors >/dev/null 2>&1 || true
for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights; do
  az provider register --namespace "$ns" --wait --only-show-errors >/dev/null 2>&1 || true
  printf '  %-36s %s\n' "$ns" "$(az provider show --namespace "$ns" --query registrationState -o tsv)"
done

say "resource group $RG ($LOC)"
az group create -n "$RG" -l "$RG_LOC" --only-show-errors -o none

say "container registry $ACR"
if ! az acr show -n "$ACR" -g "$RG" --only-show-errors >/dev/null 2>&1; then
  az acr create -n "$ACR" -g "$RG" --sku Basic --admin-enabled true -l "$LOC" --only-show-errors -o none
fi
echo "  -> $ACR.azurecr.io"

say "using locally built image already pushed to ACR"

echo "  -> $ACR.azurecr.io/$IMG"

say "container apps environment"
az containerapp env create -n "$ENVN" -g "$RG" -l "$LOC" --logs-destination none --only-show-errors -o none

say "container app (0.5 vCPU / 1 GiB, always-on)"
ACR_PASS=$(az acr credential show -n "$ACR" --query "passwords[0].value" -o tsv)
az containerapp create \
  -n "$APP" -g "$RG" --environment "$ENVN" \
  --image "$ACR.azurecr.io/$IMG" \
  --registry-server "$ACR.azurecr.io" --registry-username "$ACR" --registry-password "$ACR_PASS" \
  --target-port 5000 --ingress external \
  --cpu 0.5 --memory 1.0Gi --min-replicas 1 --max-replicas 3 \
  --secrets "sh-id=$SH_CLIENT_ID" "sh-secret=$SH_CLIENT_SECRET" "db-url=$DATABASE_URL" \
  --env-vars SH_CLIENT_ID=secretref:sh-id SH_CLIENT_SECRET=secretref:sh-secret \
             DATABASE_URL=secretref:db-url WEB_CONCURRENCY=2 \
  --only-show-errors -o none

FQDN=$(az containerapp show -n "$APP" -g "$RG" --query "properties.configuration.ingress.fqdn" -o tsv)
say "DONE"
echo "  LIVE URL:  https://$FQDN"
echo
echo "  Verify:    curl https://$FQDN/api/health"
echo "  APK:       flutter build apk --release --dart-define=BACKEND_URL=https://$FQDN"
