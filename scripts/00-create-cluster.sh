#!/usr/bin/env bash
# Creates a 2-node EKS cluster (t3.large, managed nodegroup).
# Runtime: ~15 minutes.
#
# Uses an eksctl config file (rather than bare CLI flags) because the Datadog
# AWS org enforces a Service Control Policy that denies eks:CreateCluster
# unless upgradePolicy.supportType is explicitly set to STANDARD — bare
# `eksctl create cluster` defaults to EXTENDED support and gets denied.
# See: https://datadoghq.atlassian.net/wiki/spaces/TS/pages/2295038121/Creating+EKS+Cluster+Sandboxes
set -euo pipefail

REGION="${REGION:-us-west-2}"
CLUSTER_NAME="${CLUSTER_NAME:-obs-demo}"
K8S_VERSION="${K8S_VERSION:-1.34}"

echo "==> Creating EKS cluster '${CLUSTER_NAME}' in ${REGION}..."
echo "    This takes ~15 minutes."

CONFIG_FILE="$(mktemp)"
trap 'rm -f "${CONFIG_FILE}"' EXIT

cat > "${CONFIG_FILE}" <<EOF
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: ${CLUSTER_NAME}
  region: ${REGION}
  version: "${K8S_VERSION}"

iam:
  withOIDC: true

upgradePolicy:
  supportType: STANDARD

managedNodeGroups:
  - name: standard-nodes
    instanceType: t3.large
    desiredCapacity: 2
    minSize: 2
    maxSize: 4
EOF

eksctl create cluster -f "${CONFIG_FILE}"

echo ""
echo "==> Cluster ready. Nodes:"
kubectl get nodes -o wide
