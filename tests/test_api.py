import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(autouse=True)
def disable_periodic_checks(monkeypatch):
    """Os testes dos pedidos manuais não devem iniciar tráfego automático."""
    monkeypatch.setenv("CHECK_INTERVAL_SECONDS", "0")


@pytest.mark.parametrize("code, expected", [(200, "up"), (302, "up"), (404, "down"), (503, "down")])
def test_check_status_and_persistence(tmp_path, code, expected):
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(code, headers={"location": "https://other.example"})

    database = tmp_path / "checks.db"
    api = create_app(database, "https://example.com", httpx.MockTransport(respond))
    with TestClient(api) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/checks").json() == []
        response = client.post("/checks")
        assert response.status_code == 201
        check = response.json()
        assert check["status"] == expected
        assert check["status_code"] == code
        assert check["error"] is None
        assert check["latency_ms"] >= 0
        assert requests == ["https://example.com"]  # Redirects are not followed.
    with TestClient(create_app(database, "https://example.com")) as restarted:
        assert restarted.get("/checks").json() == [check]


@pytest.mark.parametrize("failure, expected", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection_error")])
def test_network_failures_are_recorded(tmp_path, failure, expected):
    def fail(request):
        raise failure("simulated", request=request)

    with TestClient(create_app(tmp_path / "checks.db", "https://example.com", httpx.MockTransport(fail))) as client:
        response = client.post("/checks")
        assert response.status_code == 201
        check = response.json()
        assert check["status"] == "down"
        assert check["status_code"] is None
        assert check["error"] == expected
        assert client.get("/checks").json() == [check]


def test_history_order_and_limit(tmp_path):
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    with TestClient(create_app(tmp_path / "checks.db", "https://example.com", transport)) as client:
        client.post("/checks")
        newest = client.post("/checks").json()
        assert client.get("/checks?limit=1").json() == [newest]
        assert client.get("/checks?limit=0").status_code == 422
        assert client.get("/checks?limit=101").status_code == 422


@pytest.mark.parametrize("target", ["file:///etc/passwd", "https://user:password@example.com", "invalid"])
def test_invalid_configuration_is_rejected(tmp_path, target):
    with pytest.raises(ValueError):
        create_app(tmp_path / "checks.db", target)
