"""Postgres smoke test — NOT run in the default suite.

Run explicitly (needs a real Postgres; see README):
    TAVESSA_TEST_POSTGRES=1 python -m pytest tests/test_postgres_smoke.py -q
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TAVESSA_TEST_POSTGRES") != "1",
    reason="Needs a real Postgres (set TAVESSA_TEST_POSTGRES=1)",
)


def test_postgres_select_1():
    import pgserver

    server = pgserver.get_server("/tmp/tavessa-pgserver-test")
    uri = server.get_uri()
    from sqlalchemy import create_engine, text

    engine = create_engine(uri)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    server.cleanup()
