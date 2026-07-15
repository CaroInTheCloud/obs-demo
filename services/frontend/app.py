import os
import time
import uuid
import logging
import traceback

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

SERVICE_NAME = "frontend"
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend.demo.svc.cluster.local:8000")


class DemoFormatter(logging.Formatter):
    """Plain-text formatter matching the demo log format.
    Intentionally NOT JSON — the whole point is parsing unstructured logs."""

    def formatTime(self, record, datefmt=None):
        ct = time.localtime(record.created)
        t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
        return f"{t},{int(record.msecs):03d}"

    def format(self, record):
        record.asctime = self.formatTime(record)
        return f"{record.asctime} {record.levelname:<5} [{SERVICE_NAME}] {record.getMessage()}"


_handler = logging.StreamHandler()
_handler.setFormatter(DemoFormatter())
log = logging.getLogger(SERVICE_NAME)
log.addHandler(_handler)
log.setLevel(logging.INFO)
log.propagate = False

app = FastAPI()

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests",
    ["method", "path", "status", "service"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds",
    ["method", "path", "service"],
)
IN_FLIGHT = Gauge(
    "http_requests_in_flight", "Number of in-flight HTTP requests",
    ["service"],
)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    rid = str(uuid.uuid4())[:8]
    request.state.request_id = rid
    client = request.client.host if request.client else "unknown"
    path = request.url.path
    method = request.method
    start = time.time()

    IN_FLIGHT.labels(service=SERVICE_NAME).inc()
    log.info(
        f"request_id={rid} method={method} path={path} status=received client={client}"
    )

    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as exc:
        elapsed = time.time() - start
        latency_ms = int(elapsed * 1000)
        log.error(
            f"request_id={rid} method={method} path={path} status=500 "
            f"latency_ms={latency_ms} client={client} error=\"unhandled exception: {exc}\"\n"
            + traceback.format_exc()
        )
        IN_FLIGHT.labels(service=SERVICE_NAME).dec()
        REQUEST_COUNT.labels(method=method, path=path, status=500, service=SERVICE_NAME).inc()
        REQUEST_LATENCY.labels(method=method, path=path, service=SERVICE_NAME).observe(elapsed)
        return Response(content="Internal server error", status_code=500)

    elapsed = time.time() - start
    latency_ms = int(elapsed * 1000)
    status = response.status_code

    IN_FLIGHT.labels(service=SERVICE_NAME).dec()
    REQUEST_COUNT.labels(method=method, path=path, status=status, service=SERVICE_NAME).inc()
    REQUEST_LATENCY.labels(method=method, path=path, service=SERVICE_NAME).observe(elapsed)

    if status >= 500:
        log.error(
            f"request_id={rid} method={method} path={path} status={status} "
            f"latency_ms={latency_ms} client={client} error=\"upstream returned {status}\""
        )
    else:
        log.info(
            f"request_id={rid} method={method} path={path} status={status} "
            f"latency_ms={latency_ms} client={client}"
        )

    return response


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def index(request: Request):
    rid = request.state.request_id
    backend_status = None
    backend_data = None
    error_msg = None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BACKEND_URL}/work")
            backend_status = resp.status_code
            if resp.status_code == 200:
                backend_data = resp.json()
            else:
                error_msg = f"backend returned {resp.status_code}"
    except httpx.TimeoutException:
        backend_status = 504
        error_msg = "backend request timed out"
        log.error(
            f"request_id={rid} backend_call=failed error=\"{error_msg}\"\n"
            + traceback.format_exc()
        )
    except Exception as exc:
        backend_status = 503
        error_msg = str(exc)
        log.error(
            f"request_id={rid} backend_call=failed error=\"{error_msg}\"\n"
            + traceback.format_exc()
        )

    status_code = 200 if backend_status == 200 else 502

    row_html = ""
    if backend_data and "row" in backend_data:
        r = backend_data["row"]
        row_html = (
            f"<p><strong>DB row:</strong> id={r.get('id')} "
            f"event_type={r.get('event_type')} value={r.get('value')}</p>"
        )

    error_html = f'<p style="color:red"><strong>Error:</strong> {error_msg}</p>' if error_msg else ""

    html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Obs Demo — Frontend</title>
  <style>body{{font-family:monospace;max-width:640px;margin:40px auto;padding:0 16px}}</style>
</head>
<body>
  <h2>Observability Demo &mdash; Frontend</h2>
  <p><strong>Backend status:</strong> {backend_status}</p>
  {row_html}
  {error_html}
  <hr/>
  <small>
    Metrics endpoint: <a href="/metrics">/metrics</a> &nbsp;|&nbsp;
    Health: <a href="/healthz">/healthz</a>
  </small>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=status_code)
