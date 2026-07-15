"""Continuous load generator — hits the frontend at a configurable RPS forever."""
import os
import time
import logging

import requests

SERVICE_NAME = "loadgen"
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://frontend.demo.svc.cluster.local:8080")
RPS          = float(os.getenv("RPS", "5"))


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

interval = 1.0 / RPS
log.info(f"load_generator=started target={FRONTEND_URL} rps={RPS} interval_ms={int(interval*1000)}")

session = requests.Session()
total_requests = 0
total_errors   = 0

while True:
    loop_start = time.time()
    try:
        t0   = time.time()
        resp = session.get(f"{FRONTEND_URL}/", timeout=10)
        latency_ms = int((time.time() - t0) * 1000)
        total_requests += 1

        if resp.status_code >= 500:
            total_errors += 1
            log.error(
                f"method=GET path=/ status={resp.status_code} latency_ms={latency_ms} "
                f"error=\"frontend returned {resp.status_code}\" "
                f"total_requests={total_requests} total_errors={total_errors}"
            )
        elif resp.status_code >= 400:
            log.warning(
                f"method=GET path=/ status={resp.status_code} latency_ms={latency_ms} "
                f"total_requests={total_requests}"
            )
        else:
            log.info(
                f"method=GET path=/ status={resp.status_code} latency_ms={latency_ms} "
                f"total_requests={total_requests}"
            )

    except requests.exceptions.Timeout:
        total_requests += 1
        total_errors   += 1
        log.error(
            f"method=GET path=/ status=timeout latency_ms=10000 "
            f"error=\"request timed out\" total_errors={total_errors}"
        )
    except Exception as exc:
        total_requests += 1
        total_errors   += 1
        log.error(
            f"method=GET path=/ status=error latency_ms=0 "
            f"error=\"{exc}\" total_errors={total_errors}"
        )

    elapsed    = time.time() - loop_start
    sleep_time = max(0.0, interval - elapsed)
    time.sleep(sleep_time)
