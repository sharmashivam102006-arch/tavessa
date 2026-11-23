"""Dev-only demo QR token generator.

Creates (or rotates) the active token for one Zaika table and prints
the full guest URL ONCE. Refuses to run when ENVIRONMENT=production.

Run from ``backend/``:
    python -m app.seed.demo_tokens --table 5
    # -> http://localhost:5173/t/<raw-token>  (expires in 30 days)

Demo tokens expire in 30 days — PROVISIONAL pending decision D5.
Never commit a generated token; regenerate it whenever it leaks.
"""
import argparse
import sys

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import DiningTable, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import DEMO_TOKEN_EXPIRY_DAYS, create_table_token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a demo QR token for a Zaika table.")
    parser.add_argument("--table", type=int, required=True, help="Table number, e.g. 5")
    parser.add_argument("--frontend-url", default=settings.frontend_url,
                        help="Base URL for the guest link (default: FRONTEND_URL)")
    args = parser.parse_args(argv)

    if settings.is_production:
        print("Refusing to generate a demo token with ENVIRONMENT=production.",
              file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        # Make sure Zaika + the table exist first (seed is idempotent).
        seed_zaika(db)
        restaurant = (
            db.execute(select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG))
            .scalars()
            .first()
        )
        table = (
            db.execute(
                select(DiningTable).where(
                    DiningTable.restaurant_id == restaurant.id,
                    DiningTable.number == args.table,
                )
            )
            .scalars()
            .first()
        )
        if table is None:
            print(f"No table {args.table} for Zaika.", file=sys.stderr)
            return 1
        raw, row = create_table_token(db, table)
    finally:
        db.close()

    print(f"Demo token for Zaika Table {args.table} (expires in "
          f"{DEMO_TOKEN_EXPIRY_DAYS} days — provisional, pending D5):")
    print(f"  {args.frontend_url.rstrip('/')}/t/{raw}")
    print("This URL is shown ONCE — the raw token is stored only as a hash.")
    print("Do not commit it. Rotate it with this command if it leaks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
