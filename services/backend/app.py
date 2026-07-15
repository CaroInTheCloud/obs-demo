import os
import sys
import time
import uuid
import random
import logging
import traceback
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

import db as database

SERVICE_NAME = "backend"
ERROR_RATE    = float(os.getenv("ERROR_RATE",    "0.05"))
CPU_INTENSITY = int(os.getenv("CPU_INTENSITY",   "0"))
CRASH_ON_BOOT = os.getenv("CRASH_ON_BOOT", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Plain-text logger — intentionally NOT JSON (the point of the demo).
# Format: TIMESTAMP LEVEL [service] key=val key=val …
# ---------------------------------------------------------------------------

class DemoFormatter(logging.Formatter):
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


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Chaos state (in-memory flag, thread-safe for single-replica demo)
# ---------------------------------------------------------------------------

_healthy = True


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    if CRASH_ON_BOOT:
        log.error("CRASH_ON_BOOT=true — exiting with status 1")
        sys.exit(1)
    log.info(f"startup service={SERVICE_NAME} error_rate={ERROR_RATE} cpu_intensity={CPU_INTENSITY}")
    database.wait_for_db()
    database.init_db()
    yield
    log.info(f"shutdown service={SERVICE_NAME}")


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    rid = str(uuid.uuid4())[:8]
    request.state.request_id = rid
    client = request.client.host if request.client else "unknown"
    path   = request.url.path
    method = request.method
    start  = time.time()

    IN_FLIGHT.labels(service=SERVICE_NAME).inc()
    log.info(
        f"request_id={rid} method={method} path={path} status=received client={client}"
    )

    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as exc:
        elapsed    = time.time() - start
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

    elapsed    = time.time() - start
    latency_ms = int(elapsed * 1000)
    status     = response.status_code

    IN_FLIGHT.labels(service=SERVICE_NAME).dec()
    REQUEST_COUNT.labels(method=method, path=path, status=status, service=SERVICE_NAME).inc()
    REQUEST_LATENCY.labels(method=method, path=path, service=SERVICE_NAME).observe(elapsed)

    if status >= 500:
        log.error(
            f"request_id={rid} method={method} path={path} status={status} "
            f"latency_ms={latency_ms} client={client} error=\"returned {status}\""
        )
    else:
        log.info(
            f"request_id={rid} method={method} path={path} status={status} "
            f"latency_ms={latency_ms} client={client}"
        )
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    if not _healthy:
        return JSONResponse({"status": "unhealthy", "service": SERVICE_NAME}, status_code=503)
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/work")
async def work(request: Request):
    rid = request.state.request_id

    # Inject latency (10–200 ms baseline)
    sleep_ms = random.randint(10, 200)
    time.sleep(sleep_ms / 1000)

    # CPU spike mode — burn cycles proportional to CPU_INTENSITY
    if CPU_INTENSITY > 0:
        n = CPU_INTENSITY * 500_000
        acc = 0
        for i in range(n):
            acc += i * i

    # Random error injection — produces multi-line stack traces in logs
    if random.random() < ERROR_RATE:
        reasons = [
            "db timeout",
            "connection pool exhausted",
            "query plan error",
            "lock wait timeout",
            "serialization failure",
        ]
        reason = random.choice(reasons)
        try:
            raise RuntimeError(reason)
        except RuntimeError:
            log.error(
                f"request_id={rid} work=failed error=\"{reason}\"\n"
                + traceback.format_exc()
            )
        return JSONResponse({"error": reason}, status_code=500)

    try:
        row = database.do_work_query()
    except Exception as exc:
        log.error(
            f"request_id={rid} db_query=failed error=\"{exc}\"\n"
            + traceback.format_exc()
        )
        return JSONResponse({"error": "db error"}, status_code=500)

    log.info(
        f"request_id={rid} work=ok db_row_id={row.get('id')} "
        f"sleep_ms={sleep_ms} cpu_intensity={CPU_INTENSITY}"
    )
    return {"status": "ok", "row": row, "sleep_ms": sleep_ms}


# ---------------------------------------------------------------------------
# Chaos endpoints — all under /chaos/* prefix, never fire automatically
# ---------------------------------------------------------------------------

@app.post("/chaos/oom")
async def chaos_oom():
    """Allocate memory in a background thread until OOMKilled."""
    log.warning("chaos=oom triggered — allocating memory until kernel OOM-kills this pod")

    def _eat_memory():
        chunks = []
        mb = 0
        while True:
            chunks.append(bytearray(10 * 1024 * 1024))  # 10 MB per step
            mb += 10
            log.warning(f"chaos=oom allocated_mb={mb}")
            time.sleep(0.2)

    t = threading.Thread(target=_eat_memory, daemon=True)
    t.start()
    return {"status": "oom_allocation_started", "note": "pod will be OOMKilled within seconds"}


@app.post("/chaos/crash")
async def chaos_crash():
    """Hard-exit immediately — pod enters CrashLoopBackOff."""
    log.error("chaos=crash triggered — calling os._exit(1) now")
    import os as _os
    _os._exit(1)


@app.post("/chaos/unhealthy")
async def chaos_unhealthy():
    """Flip /healthz to 503 — pod goes NotReady then gets restarted by liveness probe."""
    global _healthy
    _healthy = False
    log.warning("chaos=unhealthy triggered — /healthz will return 503 until /chaos/healthy is called")
    return {"status": "healthz_set_unhealthy"}


@app.post("/chaos/healthy")
async def chaos_healthy():
    """Restore /healthz to 200."""
    global _healthy
    _healthy = True
    log.info("chaos=healthy triggered — /healthz restored to 200")
    return {"status": "healthz_restored"}
