# Observability Comparison Demo — EKS

A self-contained Kubernetes workload designed to run on AWS EKS while **Datadog** and
**Grafana Cloud** agents observe it simultaneously. The app's job is to generate
realistic, continuous infrastructure metrics and logs so you can compare each
platform's ingestion, parsing, dashboarding, and alerting side-by-side.

**Two signal sources your agents will pick up automatically:**

1. **Prometheus metrics** — each service exposes `/metrics` (request count, latency
   histogram, in-flight gauge). Datadog autodiscovers these via pod annotations;
   Grafana Alloy scrapes them natively.
2. **Plain-text stdout logs** — deliberately unstructured (not JSON). Every log line
   contains parseable fields (`request_id`, `method`, `path`, `status`, `latency_ms`,
   `error`) embedded in free text. This is the demo's centerpiece: you write a
   grok/parse rule on each platform and compare the effort and result.

---

## Prerequisites

| Tool       | Notes                                      |
|------------|--------------------------------------------|
| `awscli`   | Configured with a sandbox account          |
| `eksctl`   | v0.170+                                    |
| `kubectl`  | Any recent version                         |
| `helm`     | Needed only when installing agent sidecars |
| `docker`   | Buildx enabled (for `--platform linux/amd64`) |

Your IAM user/role needs permissions for: EKS, EC2 (VPC, nodegroups), ECR, IAM
(eksctl creates roles), CloudFormation.

> **Datadog employees using a sandbox account:** two org-level constraints will bite you
> here, both already handled by `scripts/00-create-cluster.sh`:
> 1. A Service Control Policy denies `eks:CreateCluster` unless the cluster explicitly
>    requests `upgradePolicy.supportType: STANDARD` (bare `eksctl create cluster` defaults
>    to the pricier `EXTENDED` support and gets an explicit deny). See the
>    [Creating EKS Cluster Sandboxes](https://datadoghq.atlassian.net/wiki/spaces/TS/pages/2295038121/Creating+EKS+Cluster+Sandboxes)
>    runbook.
> 2. Shared sandbox accounts often sit at the default AWS quota of 5 VPCs per region.
>    If cluster creation fails with `CREATE_FAILED` on `VPC`/`InternetGateway`/`EIP`
>    citing a "maximum ... has been reached" error, switch to a region with headroom
>    via `REGION=us-west-1 bash scripts/00-create-cluster.sh` (check
>    `aws ec2 describe-vpcs --region <region> --query 'length(Vpcs)'` first). Don't
>    delete other people's VPCs in a shared account to make room.

> **Datadog employees:** Docker Desktop is deprecated and its installer requires admin
> rights most corporate Macs don't have. Use [Colima](https://datadoghq.atlassian.net/wiki/spaces/TS/pages/6635717420/Setting+Up+Colima+A+Lightweight+Docker+Alternative+for+macOS)
> instead — it installs via Homebrew with no sudo required and provides a drop-in
> `docker` CLI, `docker compose`, and `docker buildx` (including `--platform linux/amd64`
> builds via Rosetta emulation on Apple Silicon).

**Account-specific values you will fill in:** None — the scripts derive your account
ID automatically via `aws sts get-caller-identity`.

---

## Local testing (optional, recommended first)

Before touching AWS, sanity-check the app itself with Docker Compose:

```bash
docker compose up -d --build
```

This builds and starts `postgres`, `backend`, `frontend`, and `loadgen`, wired together
by service name (no Kubernetes needed). Once it's up:

```bash
open http://localhost:8080          # frontend page, refreshes on each load
curl http://localhost:8080/metrics  # frontend Prometheus metrics
curl http://localhost:8000/metrics  # backend Prometheus metrics
docker compose logs -f              # tail plain-text logs from all services
docker compose ps                   # check container status
```

Chaos endpoints work locally too, e.g.:

```bash
curl -X POST http://localhost:8000/chaos/unhealthy
curl http://localhost:8000/healthz   # 503
curl -X POST http://localhost:8000/chaos/healthy
```

Tear down when done:

```bash
docker compose down
```

This only validates the app itself — it doesn't cover EKS-specific things like ECR
pushes, IAM, or agent autodiscovery via pod annotations. Follow the run order below for
the full picture.

---

## Run order

### Step 0 — Clone and review

```bash
cd ~/obs-demo   # or wherever you placed the repo
```

Optionally adjust defaults at the top of each script:
- `REGION` (default: `us-west-2`)
- `CLUSTER_NAME` (default: `obs-demo`)

### Step 1 — Create the EKS cluster (~15 min)

```bash
bash scripts/00-create-cluster.sh
```

Creates a managed 2-node cluster (`t3.large`). `eksctl` also configures your
`~/.kube/config` automatically.

### Step 2 — Build and push images to ECR (~5 min)

```bash
bash scripts/01-ecr-push.sh
```

Creates three ECR repos (`obs-demo/frontend`, `obs-demo/backend`, `obs-demo/loadgen`),
builds `linux/amd64` images, and pushes them. Prints `ECR_REGISTRY` at the end.

Export it for the next step:

```bash
export ECR_REGISTRY=<printed value>   # e.g. 123456789012.dkr.ecr.us-west-2.amazonaws.com
```

### Step 3 — Deploy the app (~3 min)

```bash
bash scripts/02-deploy.sh
```

Applies manifests in dependency order (namespace → postgres → backend → frontend →
loadgen), waits for each rollout, then prints pod status.

### Step 4 — Verify the app is healthy

```bash
kubectl get pods -n demo
```

Expected output (all `Running`, loadgen driving traffic immediately):

```
NAME                        READY   STATUS    RESTARTS   AGE
backend-xxxxx               1/1     Running   0          2m
frontend-xxxxx              1/1     Running   0          2m
loadgen-xxxxx               1/1     Running   0          2m
postgres-xxxxx              1/1     Running   0          3m
```

Tail all logs to confirm traffic is flowing:

```bash
kubectl logs -n demo -l env=demo -f --max-log-requests=10
```

You should see a stream of plain-text lines like:

```
2026-07-09 14:32:07,412 INFO  [loadgen]  method=GET path=/ status=200 latency_ms=87 total_requests=12
2026-07-09 14:32:07,501 INFO  [frontend] request_id=a1b2c3 method=GET path=/ status=200 latency_ms=74 client=10.0.1.7
2026-07-09 14:32:07,540 ERROR [backend]  request_id=d4e5f6 method=GET path=/work status=500 latency_ms=118 client=10.0.1.4 error="db timeout"
```

---

## Connect Datadog

Install the Agent via Helm. Avoid pasting your API key directly into shell history or
chat — write it to a local file first:

```bash
echo -n '<your-api-key>' > ~/.dd-api-key-obs-demo && chmod 600 ~/.dd-api-key-obs-demo
```

Then install:

```bash
helm repo add datadog https://helm.datadoghq.com
helm repo update datadog

helm install datadog-agent datadog/datadog \
  --namespace datadog --create-namespace \
  --set datadog.apiKey="$(cat ~/.dd-api-key-obs-demo)" \
  --set datadog.site=datadoghq.com \
  --set datadog.clusterName=obs-demo \
  --set datadog.logs.enabled=true \
  --set datadog.logs.containerCollectAll=true \
  --set datadog.prometheusScrape.enabled=true \
  --set datadog.processAgent.enabled=true \
  --set datadog.processAgent.processCollection=true \
  --set datadog.orchestratorExplorer.enabled=true
```

`datadog.prometheusScrape.enabled=true` is what makes the Cluster Agent autodiscover
the `prometheus.io/scrape` annotations already on `frontend`/`backend` — without it,
Datadog won't pick up the `/metrics` endpoints. `datadog.logs.containerCollectAll=true`
collects stdout from every pod in the cluster, unstructured as-is. `datadog.clusterName`
is **required** for per-pod Orchestrator Explorer data (see troubleshooting note below) —
set it to match your `CLUSTER_NAME`.

Verify it's working:

```bash
kubectl get pods -n datadog
# All pods should reach Running with all containers ready.

# Pick a node agent pod and confirm openmetrics checks + log shipping:
kubectl exec -n datadog <datadog-agent-pod> -c agent -- agent status
# Look for "openmetrics" instances with "config.source: prometheus_pods:..." and [OK],
# and a "Logs Agent" section with LogsSent > 0.
```

For a different site (EU, US3/US5, etc.), swap `datadog.site` — see
[Datadog site parameters](https://docs.datadoghq.com/getting_started/site/).

> **Troubleshooting: cluster/nodes show up in Datadog but no pods.** The cluster and
> nodes come from the Cluster Agent (cluster-scoped resources: Nodes, Deployments,
> ReplicaSets). Individual **pods** in
> [Orchestrator Explorer](https://app.datadoghq.com/orchestrator/pod) come from a
> separate node-level `orchestrator_pod` check, which silently no-ops if
> `datadog.clusterName` isn't set — it errors internally with "orchestrator check is
> configured but the cluster name is empty" (visible via
> `kubectl logs -n datadog <pod> -c agent | grep orchestrator`), but nothing about that
> failure is visible in `agent status` at a glance. If you installed without
> `clusterName` set, fix it with:
> ```bash
> helm upgrade datadog-agent datadog/datadog -n datadog --reuse-values \
>   --set datadog.clusterName=obs-demo
> ```

---

## Connect Grafana Cloud

Go to your Grafana Cloud stack > **Connections > Add new connection > Kubernetes** — it
generates a ready-to-run `helm upgrade --install` command for the `grafana/k8s-monitoring`
chart, pre-filled with your stack's exact Prometheus/Loki/Tempo endpoints, instance IDs, and
an access policy token. **Never paste that generated command (with the real token) into a
chat tool or commit it to git** — the token grants write access to your stack. Save the token
to a local file first:

```bash
echo -n '<your-access-policy-token>' > ~/.grafana-token-obs-demo && chmod 600 ~/.grafana-token-obs-demo
```

Take the generated values (everything under `--values -` in the heredoc) and save it as a
local `grafana-values.yaml`, but replace every literal token value with a shell substitution
so the file never has the secret baked in when regenerated:

```bash
# Inside grafana-values.yaml, wherever the generated command has a literal `password: glc_...`,
# use this instead so it's filled in at file-generation time, not committed to disk in the clear:
password: $(cat ~/.grafana-token-obs-demo)
```

Also set `cluster.name` (and `opencost.opencost.exporter.defaultClusterId`, if `opencost` is
enabled) to your actual `CLUSTER_NAME` — the generated command defaults this to a placeholder
like `my-cluster`.

Then render and install:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm upgrade --install --rollback-on-failure --timeout 300s grafana-k8s-monitoring \
  grafana/k8s-monitoring --version "^4" --namespace monitoring --create-namespace \
  --values grafana-values.yaml
```

(`--rollback-on-failure` replaces the deprecated `--atomic` flag in recent Helm versions.)

This installs Grafana Alloy as several components — `alloy-metrics` (StatefulSet),
`alloy-logs` (DaemonSet), `alloy-singleton` (Deployment, cluster events + manifests),
plus `kube-state-metrics`, `node-exporter`, and `opencost` — collecting metrics, logs,
Kubernetes events, and cost data in parallel with the Datadog Agent already running.

Verify it's working:

```bash
kubectl get pods -n monitoring
# All pods should reach Running.

# Check for auth failures (401/403) across every monitoring pod — a bad token shows up
# here immediately, `agent`/`alloy` logs don't hide it the way Datadog's checks can:
for pod in $(kubectl get pods -n monitoring -o jsonpath='{.items[*].metadata.name}'); do
  kubectl logs -n monitoring "$pod" --all-containers --tail=100 | grep -i "401\|403\|unauthorized\|forbidden"
done
```

Then confirm data in Grafana Cloud's **Kubernetes Monitoring** app (Infrastructure >
Kubernetes) — cluster, nodes, and pods should all populate within a few minutes, since
(unlike Datadog) this chart's default values collect pod-level data from day one without
an extra cluster-name flag.

---

## Reach the frontend

```bash
kubectl port-forward svc/frontend -n demo 8080:8080
```

Open [http://localhost:8080](http://localhost:8080). The page shows the latest
backend response (including a random DB row) and refreshes on each load.

The `/metrics` endpoint for each service is also reachable after port-forwarding:

```bash
# Frontend metrics
curl http://localhost:8080/metrics

# Backend metrics (port-forward separately)
kubectl port-forward svc/backend -n demo 8000:8000
curl http://localhost:8000/metrics
```

---

## Env vars you can tweak live

Use `kubectl set env` to change these on a running deployment — the pod restarts and
picks up the new value immediately.

| Var            | Service  | Default | Effect                                                           |
|----------------|----------|---------|------------------------------------------------------------------|
| `ERROR_RATE`   | backend  | `0.05`  | Fraction of `/work` requests that return 500. Set to `0.5` for a spike. |
| `CPU_INTENSITY`| backend  | `0`     | Each unit adds 500k loop iterations per request. `5` causes visible CPU throttle. |
| `RPS`          | loadgen  | `5`     | Requests per second to the frontend. Raise to increase throughput. |

### Spike CPU (demo: throttling / limits panel)

```bash
kubectl set env deployment/backend -n demo CPU_INTENSITY=10
# Watch CPU climb toward the 500m limit
kubectl top pods -n demo
# Restore
kubectl set env deployment/backend -n demo CPU_INTENSITY=0
```

### Raise error rate (demo: error-rate panel)

```bash
kubectl set env deployment/backend -n demo ERROR_RATE=0.5
# Restore
kubectl set env deployment/backend -n demo ERROR_RATE=0.05
```

### Increase load (demo: throughput)

```bash
kubectl set env deployment/loadgen -n demo RPS=20
# Restore
kubectl set env deployment/loadgen -n demo RPS=5
```

---

## Demoing failures

> **Recovery is always one command away.** Read the "Revert" step before triggering
> anything. The loadgen keeps running throughout — failures in the backend visibly
> cascade to the frontend error rate, giving each platform a realistic incident to
> display.

---

### 1. OOMKilled

**What it shows:** pod killed by the kernel, restart count increments,
`kube_pod_container_status_last_terminated_reason="OOMKilled"`.

**Trigger:**
```bash
kubectl exec -n demo deployment/backend -- \
  curl -s -X POST http://localhost:8000/chaos/oom
```

**Watch:**
```bash
kubectl get pods -n demo -w
# Look for: STATUS=OOMKilled then Terminating then Running (restart count +1)
```

**Describe for reason:**
```bash
kubectl describe pod -n demo -l app=backend | grep -A5 "Last State:"
```

**Revert:** Nothing needed — Kubernetes restarts the pod automatically. The memory is
freed when the container is killed.

**Metric to look for:** `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}`

---

### 2. CrashLoopBackOff (via manifest)

**What it shows:** process exits non-zero immediately on boot; Kubernetes keeps
restarting with exponential backoff. Restart count climbs indefinitely.

**Trigger:**
```bash
sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" k8s/chaos/backend-crashloop.yaml \
  | kubectl apply -f -
```

**Watch:**
```bash
kubectl get pods -n demo -w
# Look for: STATUS=CrashLoopBackOff, RESTARTS climbing
```

**Revert (restore baseline):**
```bash
sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" k8s/backend.yaml \
  | kubectl apply -f -
kubectl rollout status deployment/backend -n demo
```

**Metric to look for:** `kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}`

**Bonus — crash a running pod on demand:**
```bash
kubectl exec -n demo deployment/backend -- \
  curl -s -X POST http://localhost:8000/chaos/crash
```

---

### 3. Failing health probe → NotReady → pod restart

**What it shows:** readiness probe fails → pod pulled from Service endpoints
(frontend calls start failing) → liveness probe fails after threshold → pod
restarted. Cascading effect is visible on frontend error rate.

**Trigger:**
```bash
kubectl exec -n demo deployment/backend -- \
  curl -s -X POST http://localhost:8000/chaos/unhealthy
```

**Watch:**
```bash
kubectl get pods -n demo -w
# Look for: READY=0/1 (NotReady), then pod restart after liveness threshold (~30s)
kubectl get endpoints backend -n demo -w
# Endpoint disappears from the Service while pod is NotReady
```

**Revert (before liveness kills the pod, or after it restarts healthy):**
```bash
kubectl exec -n demo deployment/backend -- \
  curl -s -X POST http://localhost:8000/chaos/healthy
```

**Metric to look for:** `kube_pod_status_ready{condition="false"}` and
`kube_pod_container_status_restarts_total`

---

### 4. ImagePullBackOff

**What it shows:** Kubernetes cannot pull a nonexistent image tag. The pod stays
stuck — no process ever starts. Useful for showing the events stream and image-pull
failure metrics.

**Trigger:**
```bash
sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" k8s/chaos/backend-badimage.yaml \
  | kubectl apply -f -
```

**Watch:**
```bash
kubectl get pods -n demo -w
# Look for: STATUS=ImagePullBackOff or ErrImagePull
kubectl describe pod -n demo -l app=backend | grep -A10 Events:
```

**Revert:**
```bash
sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" k8s/backend.yaml \
  | kubectl apply -f -
kubectl rollout status deployment/backend -n demo
```

**Metric to look for:** `kube_pod_container_status_waiting_reason{reason="ImagePullBackOff"}`

---

### 5. Pending / Unschedulable

**What it shows:** scheduler cannot find a node that satisfies the resource request
(1000Gi memory). Pod stays `Pending` indefinitely with a `FailedScheduling` event.
Great for showing the scheduler visibility and events stream.

**Trigger:**
```bash
sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" k8s/chaos/backend-unschedulable.yaml \
  | kubectl apply -f -
```

**Watch:**
```bash
kubectl get pods -n demo -w
# Look for: STATUS=Pending (never progresses)
kubectl describe pod -n demo -l app=backend | grep -A5 "Events:"
# Shows: "0/2 nodes are available: insufficient memory"
```

**Revert:**
```bash
sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" k8s/backend.yaml \
  | kubectl apply -f -
kubectl rollout status deployment/backend -n demo
```

**Metric to look for:** `kube_pod_status_phase{phase="Pending"}` and
`kube_pod_unschedulable`

---

## What to observe in each platform

### Metrics (`/metrics`)

Both agents discover and scrape the Prometheus endpoints automatically:

- **Datadog** — uses the `prometheus.io/scrape`, `prometheus.io/port`, and
  `prometheus.io/path` pod annotations for OpenMetrics autodiscovery. No extra
  config needed once the Datadog agent is installed with admission controller enabled.
- **Grafana Alloy** — scrapes pods with those same annotations natively via
  `discovery.kubernetes` + `prometheus.scrape`.

Key metrics to build panels for:
- `http_requests_total` — counter by `service`, `path`, `status`: error rate
- `http_request_duration_seconds` — histogram: p50/p95/p99 latency
- `http_requests_in_flight` — gauge: concurrency
- `kube_pod_container_resource_limits` / `kube_pod_container_resource_requests` — for the
  utilization-vs-limits panel
- `kube_pod_container_status_restarts_total` — restart counter (chaos modes)

### Logs (stdout, plain text)

The log format is deliberately unstructured. Every line looks like:

```
2026-07-09 14:32:07,412 INFO  [backend] request_id=d4e5f6 method=GET path=/work status=200 latency_ms=43 client=10.0.1.4
2026-07-09 14:32:07,540 ERROR [backend] request_id=a1b2c3 method=GET path=/work status=500 latency_ms=118 client=10.0.1.4 error="db timeout"
```

This is the point of the comparison: each platform gives you a different tool for
parsing these lines and promoting fields to searchable/filterable attributes:

- **Datadog** — write a grok parser in a Processing Pipeline or use the log
  explorer's auto-parse. You can also use Datadog's Sensitive Data Scanner and
  remapping processors to normalize `status` to HTTP status codes.
- **Grafana Cloud (Loki)** — write a LogQL `| pattern` or `| regexp` expression, or
  configure a Promtail/Alloy pipeline stage (`regex` or `logfmt` stage, then
  `labels` stage to promote fields).

Multi-line stack traces appear on error responses — use this to compare each
platform's multi-line log stitching configuration.

---

## Teardown

When you're done:

```bash
bash scripts/99-teardown.sh
```

This deletes the `demo` namespace, all ECR repositories, and the EKS cluster.
Confirm in the AWS console (EKS, EC2, ECR, CloudFormation) that no resources remain.

> **Tip:** if you only want to reset the app without destroying the cluster, just
> delete and re-deploy the namespace:
> ```bash
> kubectl delete namespace demo
> bash scripts/02-deploy.sh
> ```
