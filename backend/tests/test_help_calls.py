"""Help calls — one open call per session (Stage 3)."""
import pytest

from tests.helpers import fresh_table_token, headers, open_session, seed


@pytest.fixture
def setup(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    open_session(client, token)
    return {"token": token}


def test_create_help_call(client, db_session, setup):
    response = client.post(
        "/api/v1/guest/help-calls",
        json={"type": "water", "note": "Two glasses please"},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 201, response.text
    call = response.json()["data"]
    assert call["type"] == "water"
    assert call["status"] == "open"
    assert call["note"]
    assert call["tableSessionId"]
    assert call["createdAt"]


def test_second_open_call_409(client, db_session, setup):
    first = client.post(
        "/api/v1/guest/help-calls",
        json={"type": "waiter"},
        headers=headers(setup["token"]),
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/guest/help-calls",
        json={"type": "water"},
        headers=headers(setup["token"]),
    )
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"]["existing_help_call_id"] == first.json()["data"]["id"]


def test_type_alias_and_case_insensitive(client, db_session, setup):
    response = client.post(
        "/api/v1/guest/help-calls",
        json={"type": "Plates_Cutlery"},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["type"] == "plates"


def test_invalid_type_422(client, db_session, setup):
    response = client.post(
        "/api/v1/guest/help-calls",
        json={"type": "dj"},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 422
    response = client.post(
        "/api/v1/guest/help-calls",
        json={},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 422


def test_help_call_requires_token(client, db_session, setup):
    response = client.post("/api/v1/guest/help-calls", json={"type": "water"})
    assert response.status_code == 401
