"""API de monitorização HTTP com histórico e verificações periódicas."""

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from threading import Event, Lock, Thread
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Query
from pydantic import BaseModel

from app.structured_logging import logger


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
    check_interval: int | None = None,
) -> FastAPI:
    database = db_path if db_path is not None else Path(os.getenv("DATABASE_PATH", "data/checks.db"))
    target = target_url if target_url is not None else os.getenv("TARGET_URL", "https://example.com")
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("TARGET_URL must be an HTTP(S) URL without embedded credentials")
    interval = check_interval if check_interval is not None else int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
    if type(interval) is not int or not 0 <= interval <= 86400:
        raise ValueError("CHECK_INTERVAL_SECONDS deve ser um inteiro entre 0 e 86400")
    check_lock = Lock()

    def periodic_checks(stop: Event):
        while not stop.is_set():
            # Uma verificação manual em curso adia este ciclo automático.
            if check_lock.acquire(blocking=False):
                try:
                    execute_check("scheduled")
                except Exception:
                    # A falha já foi registada; tentar novamente no próximo ciclo.
                    pass
                finally:
                    check_lock.release()
            if stop.wait(interval):
                break

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
        stop = Event()
        worker = None
        logger.info("monitor_started", extra={"fields": {"interval_seconds": interval, "enabled": interval > 0}})
        if interval:
            worker = Thread(target=periodic_checks, args=(stop,), name="cloudops-monitor", daemon=True)
            worker.start()
        try:
            yield
        finally:
            stop.set()
            if worker is not None:
                # Esperar pela verificação em curso sem bloquear o ciclo assíncrono.
                await asyncio.to_thread(worker.join)
            logger.info("monitor_stopped")

    api = FastAPI(
        title="CloudOps Lab",
        version="0.2.0",
        description="Monitorização HTTP manual e periódica com histórico de verificações.",
        lifespan=lifespan,
    )

    @api.get("/health")
    def health() -> dict[str, str]:
        """Confirma que a API está ativa, independentemente do serviço monitorizado."""
        return {"status": "ok"}

    @api.post("/checks", response_model=Check, status_code=201)
    def run_check():
        """Executa uma verificação manual. Respostas HTTP 2xx e 3xx contam como up."""
        with check_lock:
            return execute_check("manual")

    def execute_check(source: str):
        try:
            result = probe_and_save()
        except Exception as exc:
            logger.error("check_failed", extra={"fields": {
                "source": source, "error_type": type(exc).__name__,
            }})
            raise
        # O URL completo pode conter dados sensíveis; registar apenas o nome do host.
        logger.log(logging.INFO if result["status"] == "up" else logging.WARNING,
                   "check_completed", extra={"fields": {
                       "check_id": result["id"], "source": source,
                       "target_host": parsed.hostname, "status": result["status"],
                       "status_code": result["status_code"],
                       "latency_ms": result["latency_ms"], "error": result["error"],
                   }})
        return result

    def probe_and_save():
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
                # Medir até aos cabeçalhos, sem descarregar o corpo da resposta.
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
        """Consulta as verificações mais recentes, com um limite de resultados."""
        with closing(connect()) as connection:
            rows = connection.execute("SELECT * FROM checks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    return api


app = create_app()
