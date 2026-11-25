"""Seed Zaika + mint table tokens for E2E runs (Stage 9).

Usage:
    DATABASE_URL=postgresql+psycopg://... python scripts/seed_e2e.py [out.json]

Seeds the Zaika demo data (idempotent) and creates a fresh QR
token for tables 5, 6, 7 and 8 (revoking any previous token for
each table). Prints {"restaurant_id": ..., "tokens": {"5": ...}}
as JSON to stdout, and writes it to out.json when given.

Used by backend/scripts/verify_stage9_e2e.py (local mode) and by
backend/scripts/run_e2e_compose.sh (via `docker compose exec api
python scripts/seed_e2e.py`), so the Compose path seeds the stack
the same way the local path does.
"""
import json
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import DiningTable, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token


def main() -> int:
    url = os.environ["DATABASE_URL"]
    engine = create_engine(url, poolclass=None)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        seed_zaika(session)
        restaurant = session.execute(
            select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
        ).scalars().first()
        tokens = {}
        for number in (5, 6, 7, 8):
            table = session.execute(
                select(DiningTable).where(
                    DiningTable.restaurant_id == restaurant.id,
                    DiningTable.number == number,
                )
            ).scalars().first()
            raw, _row = create_table_token(session, table)
            tokens[number] = raw
        out = {"restaurant_id": str(restaurant.id), "tokens": tokens}
    finally:
        session.close()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as fh:
            json.dump(out, fh)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
