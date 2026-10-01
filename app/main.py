"""Local availability monitoring API. Configure the trusted target at startup."""

import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Query
from pydantic import BaseModel


class Check(BaseModel):
    id: int
    target: str
    checked_at: str
    status: Literal["up", "down"]
    status_code: int | None
    latency_ms: float
    error: str | None


def create_app(
    db_path: Path | None = None,
    target_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    database = db_path if db_path is not None else Path(os.getenv("DATABASE_PATH", "data/checks.db"))
    target = target_url if target_url is not None else os.getenv("TARGET_URL", "https://example.com")
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("TARGET_URL must be an HTTP(S) URL without embedded credentials")

    def connect():
        connection = sqlite3.connect(database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    status_code INTEGER,
                    latency_ms REAL NOT NULL,
                    error TEXT
                )
            """)
        yield

    api = FastAPI(
        title="CloudOps Lab",
        version="0.1.0",
        description="Local learning project: run an HTTP check and inspect its history.",
        lifespan=lifespan,
    )

    @api.get("/health")
    def health() -> dict[str, str]:
        """API liveness; independent of the monitored service."""
        return {"status": "ok"}

    @api.post("/checks", response_model=Check, status_code=201)
    def run_check():
        """Check the configured target once. HTTP 2xx and 3xx count as up."""
        checked_at = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        status_code = None
        error = None
        status = "down"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(5.0),
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client:
                # Measure time to response headers; do not download the response body.
                with client.stream("GET", target) as response:
                    status_code = response.status_code
                    status = "up" if 200 <= status_code < 400 else "down"
        except httpx.TimeoutException:
            error = "timeout"
        except httpx.RequestError:
            error = "connection_error"
        latency_ms = round((perf_counter() - started) * 1000, 2)
        with closing(connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO checks (target, checked_at, status, status_code, latency_ms, error) VALUES (?, ?, ?, ?, ?, ?)",
                (target, checked_at, status, status_code, latency_ms, error),
            )
            row = connection.execute("SELECT * FROM checks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    @api.get("/checks", response_model=list[Check])
    def list_checks(limit: int = Query(default=20, ge=1, le=100)):
        """Read the newest checks first, with a bounded result size."""
        with closing(connect()) as connection:
            rows = connection.execute("SELECT * FROM checks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    return api


app = create_app()
