#!/usr/bin/env bash
# Deletes the demo namespace, ECR repos (optional), and the EKS cluster.
# Run this when you're done demoing to avoid leaving AWS resources running.
set -euo pipefail

REGION="${REGION:-us-west-2}"
CLUSTER_NAME="${CLUSTER_NAME:-obs-demo}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "============================================================"
echo "  TEARDOWN: obs-demo"
echo "  Cluster:  ${CLUSTER_NAME}"
echo "  Region:   ${REGION}"
echo "  Account:  ${ACCOUNT_ID}"
echo "============================================================"
echo ""
echo "This will:"
echo "  1. Delete the 'demo' namespace (all app workloads)"
echo "  2. Delete all three ECR repositories + images"
echo "  3. Delete the EKS cluster '${CLUSTER_NAME}' (~15 min)"
echo ""
read -rp "Type 'yes' to proceed: " confirm
[[ "${confirm}" == "yes" ]] || { echo "Aborted."; exit 1; }
echo ""

# ── Delete namespace ───────────────────────────────────────────────────────
echo "==> Deleting namespace demo..."
kubectl delete namespace demo --ignore-not-found
echo "    Done."

# ── Delete ECR repos ───────────────────────────────────────────────────────
echo "==> Deleting ECR repositories..."
for svc in frontend backend loadgen; do
  aws ecr delete-repository \
    --repository-name "obs-demo/${svc}" \
    --region "${REGION}" \
    --force \
    2>/dev/null && echo "    Deleted obs-demo/${svc}" || echo "    obs-demo/${svc} not found — skipping"
done

# ── Delete EKS cluster ─────────────────────────────────────────────────────
echo ""
echo "==> Deleting EKS cluster '${CLUSTER_NAME}' (this takes ~15 minutes)..."
eksctl delete cluster --name "${CLUSTER_NAME}" --region "${REGION}"

echo ""
echo "==> Teardown complete. Verify in the AWS console that all resources are gone."
