"""Health endpoint — Stage 1 smoke tests."""


def test_health_ok(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["environment"] == "development"


def test_root_ok(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"service": "tavessa", "status": "ok"}


def test_unknown_route_is_envelope(client):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert "error" in response.json()
