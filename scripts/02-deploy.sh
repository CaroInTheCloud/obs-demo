#!/usr/bin/env bash
# Deploys all workloads to the demo namespace in dependency order.
# Requires ECR_REGISTRY to be set (printed by 01-ecr-push.sh).
set -euo pipefail

REGION="${REGION:-us-west-2}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REGISTRY="${ECR_REGISTRY:-${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(dirname "${SCRIPT_DIR}")/k8s"

echo "==> ECR_REGISTRY: ${ECR_REGISTRY}"
echo ""

# ── Namespace ──────────────────────────────────────────────────────────────
echo "==> Applying namespace..."
kubectl apply -f "${K8S_DIR}/namespace.yaml"

# ── Postgres (no image substitution needed) ────────────────────────────────
echo "==> Deploying postgres..."
kubectl apply -f "${K8S_DIR}/postgres.yaml"
echo "    Waiting for postgres to be ready..."
kubectl rollout status deployment/postgres -n demo --timeout=120s

# ── App services (substitute ECR_REGISTRY placeholder) ────────────────────
for svc in backend frontend loadgen; do
  echo "==> Deploying ${svc}..."
  sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" "${K8S_DIR}/${svc}.yaml" | kubectl apply -f -
done

echo ""
echo "==> Waiting for rollouts..."
kubectl rollout status deployment/backend  -n demo --timeout=180s
kubectl rollout status deployment/frontend -n demo --timeout=120s
kubectl rollout status deployment/loadgen  -n demo --timeout=120s

echo ""
echo "==> All deployments healthy!"
kubectl get pods -n demo -o wide

echo ""
echo "==> Access the frontend:"
echo "    kubectl port-forward svc/frontend -n demo 8080:8080"
echo "    Then open: http://localhost:8080"
echo ""
echo "==> Tail all logs:"
echo "    kubectl logs -n demo -l env=demo -f --max-log-requests=10"
