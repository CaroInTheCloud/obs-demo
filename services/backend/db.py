import os
import random
import time
import logging

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://demo:demo@postgres.demo.svc.cluster.local:5432/demo",
)

log = logging.getLogger("backend")


def wait_for_db(retries: int = 30, delay: float = 2.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            conn.close()
            log.info("db=connected attempt=%d", attempt)
            return
        except psycopg2.OperationalError as exc:
            log.warning(
                f"db_ready=false attempt={attempt}/{retries} error=\"{exc}\""
            )
            time.sleep(delay)
    raise RuntimeError("could not connect to database after %d attempts" % retries)


def init_db() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id         SERIAL PRIMARY KEY,
                    event_type VARCHAR(100) NOT NULL,
                    value      INTEGER      NOT NULL,
                    created_at TIMESTAMP    DEFAULT NOW()
                )
            """)
            cur.execute("SELECT COUNT(*) FROM events")
            count = cur.fetchone()[0]
            if count == 0:
                seed_rows = [
                    ("startup",    1),
                    ("seed_alpha", 42),
                    ("seed_beta",  17),
                    ("seed_gamma", 99),
                    ("seed_delta", 8),
                ]
                cur.executemany(
                    "INSERT INTO events (event_type, value) VALUES (%s, %s)",
                    seed_rows,
                )
                log.info(f"db_seed=complete rows={len(seed_rows)}")
            conn.commit()
    finally:
        conn.close()


def do_work_query() -> dict:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, event_type, value FROM events ORDER BY RANDOM() LIMIT 1"
            )
            row = dict(cur.fetchone() or {})

            if random.random() < 0.15:
                cur.execute(
                    "INSERT INTO events (event_type, value) VALUES (%s, %s) RETURNING id",
                    ("work_event", random.randint(1, 1000)),
                )
                new_id = cur.fetchone()["id"]
                log.info(f"db_insert=ok new_id={new_id}")

            conn.commit()
        return row
    finally:
        conn.close()
