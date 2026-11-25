"""Stage 9 — security headers (item 6) + CORS allowlist (item 5).

Headers are asserted on live responses. The allowlist comes from
settings.cors_origin_list (default: http://localhost:5173,
http://localhost:4173). No CSP is set (Vite inline assets — see
main.py comment) and no HSTS (no TLS termination in dev — Stage 10
deployment decision); both absences are pinned here so they are
deliberate, not overlooked.
"""
from app.core.config import settings
from tests.sec9 import seed

LISTED = "http://localhost:5173"
LISTED2 = "http://localhost:4173"
EVIL = "https://evil.example"


def _headers(response):
    return {k.lower(): v for k, v in response.headers.items()}


def test_security_headers_present_on_api_responses(client, db_session):
    seed(db_session)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    h = _headers(response)
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store"


def test_security_headers_present_but_no_no_store_off_api(client, db_session):
    seed(db_session)
    response = client.get("/")
    assert response.status_code == 200
    h = _headers(response)
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    # Root / docs are not /api/* — no Cache-Control: no-store there.
    assert h.get("cache-control") != "no-store"


def test_no_csp_and_no_hsts_by_decision(client, db_session):
    """Pinned deliberate absences: CSP would break the Vite
    frontend's inline assets; HSTS without TLS termination would
    be meaningless (Stage 10 reverse-proxy decision)."""
    seed(db_session)
    h = _headers(client.get("/api/v1/health"))
    assert "content-security-policy" not in h
    assert "strict-transport-security" not in h


def test_cors_listed_origin_gets_acao_and_credentials(client, db_session):
    seed(db_session)
    assert LISTED in settings.cors_origin_list
    assert LISTED2 in settings.cors_origin_list
    response = client.get("/api/v1/health", headers={"Origin": LISTED})
    h = _headers(response)
    assert h.get("access-control-allow-origin") == LISTED
    assert h.get("access-control-allow-credentials") == "true"
    # No wildcard-with-credentials anywhere.
    assert h.get("access-control-allow-origin") != "*"


def test_cors_unlisted_origin_gets_no_acao(client, db_session):
    seed(db_session)
    response = client.get("/api/v1/health", headers={"Origin": EVIL})
    h = _headers(response)
    assert "access-control-allow-origin" not in h
    assert "access-control-allow-credentials" not in h


def test_cors_preflight_listed_vs_unlisted(client, db_session):
    seed(db_session)
    preflight = {
        "Origin": LISTED,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    response = client.options("/api/v1/auth/login", headers=preflight)
    assert response.status_code == 200, response.text
    h = _headers(response)
    assert h.get("access-control-allow-origin") == LISTED
    assert h.get("access-control-allow-credentials") == "true"
    evil = dict(preflight, Origin=EVIL)
    response = client.options("/api/v1/auth/login", headers=evil)
    h = _headers(response)
    assert "access-control-allow-origin" not in h


def test_cors_allowlist_has_no_wildcard(client, db_session):
    assert "*" not in settings.cors_origin_list
    assert all(o.startswith("http://") or o.startswith("https://")
               for o in settings.cors_origin_list)
