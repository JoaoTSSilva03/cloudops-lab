"""Testes do agendamento, encerramento e registos estruturados."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.structured_logging import JsonFormatter


def wait_for_checks(client, count):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        checks = client.get("/checks").json()
        if len(checks) >= count:
            return checks
        sleep(0.02)
    pytest.fail("O monitor não guardou as verificações dentro do prazo")


def test_periodic_checks_recover_and_stop(tmp_path):
    calls = []
    unexpected_call = Event()
    stopped = Event()

    def respond(request):
        if stopped.is_set():
            unexpected_call.set()
        calls.append(monotonic())
        if len(calls) == 1:
            raise httpx.ConnectError("falha simulada", request=request)
        return httpx.Response(200)

    database = tmp_path / "checks.db"
    with TestClient(create_app(database, "https://example.com", httpx.MockTransport(respond), 1)) as client:
        checks = wait_for_checks(client, 2)
        assert checks[0]["status"] == "up"
        assert checks[1]["error"] == "connection_error"
        assert calls[1] - calls[0] >= 0.9
        assert client.get("/health").status_code == 200
    stopped.set()
    assert not unexpected_call.wait(1.2)
    with TestClient(create_app(database, check_interval=0)) as client:
        assert len(client.get("/checks").json()) >= 2


def test_disabled_monitor_makes_no_requests(tmp_path):
    requested = Event()

    def respond(request):
        requested.set()
        return httpx.Response(200)

    with TestClient(create_app(tmp_path / "checks.db", transport=httpx.MockTransport(respond), check_interval=0)) as client:
        assert not requested.wait(0.1)
        assert client.get("/checks").json() == []
        assert client.post("/checks").status_code == 201
        assert requested.is_set()


def test_manual_check_waits_for_running_automatic_check(tmp_path):
    automatic_started = Event()
    release_automatic = Event()
    manual_attempted = Event()
    manual_reached_target = Event()
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            automatic_started.set()
            assert release_automatic.wait(5)
        else:
            manual_reached_target.set()
        return httpx.Response(200)

    api = create_app(tmp_path / "checks.db", transport=httpx.MockTransport(respond), check_interval=60)
    with TestClient(api) as client, ThreadPoolExecutor(max_workers=1) as executor:
        try:
            assert automatic_started.wait(2)
            assert client.get("/health").status_code == 200

            def manual_check():
                manual_attempted.set()
                return client.post("/checks")

            result = executor.submit(manual_check)
            assert manual_attempted.wait(2)
            assert not manual_reached_target.wait(0.1)
        finally:
            release_automatic.set()
        assert result.result(timeout=3).status_code == 201
        assert len(client.get("/checks").json()) == 2


@pytest.mark.parametrize("value", ["-1", "invalid", "0.5", "86401"])
def test_invalid_interval_is_rejected(tmp_path, monkeypatch, value):
    monkeypatch.setenv("CHECK_INTERVAL_SECONDS", value)
    with pytest.raises(ValueError):
        create_app(tmp_path / "checks.db")


def test_interval_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECK_INTERVAL_SECONDS", "1")
    with TestClient(create_app(tmp_path / "checks.db", transport=httpx.MockTransport(lambda _: httpx.Response(200)))) as client:
        assert wait_for_checks(client, 2)[0]["status"] == "up"


def test_json_logs_identify_source_and_omit_url_secrets(tmp_path, caplog):
    target = "https://example.com/private-path?token=private-token#private-fragment"
    transport = httpx.MockTransport(lambda _: httpx.Response(503))
    with TestClient(create_app(tmp_path / "checks.db", target, transport, 60)) as client:
        wait_for_checks(client, 1)
        assert client.post("/checks").status_code == 201
    lines = [JsonFormatter().format(r) for r in caplog.records if r.name == "cloudops"]
    events = [json.loads(line) for line in lines]
    completed = [e for e in events if e["event"] == "check_completed"]
    assert {e["source"] for e in completed} == {"manual", "scheduled"}
    assert all(e["level"] == "WARNING" and e["status_code"] == 503 for e in completed)
    assert all(e["target_host"] == "example.com" for e in completed)
    assert events[0]["event"] == "monitor_started"
    assert events[-1]["event"] == "monitor_stopped"
    assert all("timestamp" in e for e in events)
    assert "private-" not in "\n".join(lines)


def test_scheduler_survives_database_failure(tmp_path, monkeypatch, caplog):
    original_connect = sqlite3.connect
    attempted = 0

    def connect(*args, **kwargs):
        nonlocal attempted
        from threading import current_thread
        if current_thread().name == "cloudops-monitor":
            attempted += 1
            if attempted == 1:
                raise sqlite3.OperationalError("simulação de bloqueio")
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with TestClient(create_app(tmp_path / "checks.db", transport=httpx.MockTransport(lambda _: httpx.Response(200)), check_interval=1)) as client:
        assert wait_for_checks(client, 1)[0]["status"] == "up"
    failures = [r for r in caplog.records if r.name == "cloudops" and r.getMessage() == "check_failed"]
    assert len(failures) == 1
    assert failures[0].fields == {"source": "scheduled", "error_type": "OperationalError"}
