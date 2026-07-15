#!/usr/bin/env bash
# Creates a 2-node EKS cluster (t3.large, managed nodegroup).
# Runtime: ~15 minutes.
set -euo pipefail

REGION="${REGION:-us-west-2}"
CLUSTER_NAME="${CLUSTER_NAME:-obs-demo}"

echo "==> Creating EKS cluster '${CLUSTER_NAME}' in ${REGION}..."
echo "    This takes ~15 minutes."

eksctl create cluster \
  --name          "${CLUSTER_NAME}" \
  --region        "${REGION}"       \
  --nodegroup-name standard-nodes   \
  --node-type     t3.large          \
  --nodes         2                 \
  --nodes-min     2                 \
  --nodes-max     4                 \
  --managed                         \
  --with-oidc

echo ""
echo "==> Cluster ready. Nodes:"
kubectl get nodes -o wide
