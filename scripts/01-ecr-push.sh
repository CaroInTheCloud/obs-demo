#!/usr/bin/env bash
# Creates ECR repos, builds Docker images for linux/amd64, and pushes them.
# Run from the repo root or any directory — paths are resolved from script location.
set -euo pipefail

REGION="${REGION:-us-west-2}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

echo "==> Account:      ${ACCOUNT_ID}"
echo "==> Region:       ${REGION}"
echo "==> ECR registry: ${ECR_REGISTRY}"
echo ""

# ── Create ECR repositories ────────────────────────────────────────────────
for svc in frontend backend loadgen; do
  if aws ecr describe-repositories \
       --repository-names "obs-demo/${svc}" \
       --region "${REGION}" \
       --output text &>/dev/null; then
    echo "    Repo obs-demo/${svc} already exists — skipping create"
  else
    aws ecr create-repository \
      --repository-name "obs-demo/${svc}" \
      --region "${REGION}" \
      --image-scanning-configuration scanOnPush=false \
      --output text
    echo "    Created repo obs-demo/${svc}"
  fi
done
echo ""

# ── Docker login ───────────────────────────────────────────────────────────
echo "==> Authenticating Docker with ECR..."
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"
echo ""

# ── Build, tag, push ──────────────────────────────────────────────────────
for svc in frontend backend loadgen; do
  echo "==> Building ${svc}..."
  docker build \
    --platform linux/amd64 \
    -t "obs-demo/${svc}:latest" \
    "${REPO_ROOT}/services/${svc}"

  docker tag "obs-demo/${svc}:latest" "${ECR_REGISTRY}/obs-demo/${svc}:latest"
  docker push "${ECR_REGISTRY}/obs-demo/${svc}:latest"
  echo "    Pushed: ${ECR_REGISTRY}/obs-demo/${svc}:latest"
  echo ""
done

echo "==> All images pushed."
echo ""
echo "Export this for the deploy script:"
echo "  export ECR_REGISTRY=${ECR_REGISTRY}"
