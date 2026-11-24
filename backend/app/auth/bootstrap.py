"""Bootstrap CLI — create the FIRST owner for a restaurant.

Usage (from ``backend/``):
    python -m app.auth.bootstrap --restaurant-slug zaika --email owner@zaika.example

The password is prompted TWICE via ``getpass`` — it is never taken
from argv, never logged, and never committed. The command refuses
if ANY owner already exists for the restaurant, or if a user with
that email already exists. There is NO public owner-registration
endpoint; this CLI is the only way to create the first owner.
"""
import argparse
import getpass
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.auth.models import User
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import Restaurant
from app.services.audit_service import write_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the first owner account for a restaurant."
    )
    parser.add_argument(
        "--restaurant-slug", default="zaika", help="Restaurant slug (default: zaika)"
    )
    parser.add_argument("--email", required=True, help="Owner email address")
    args = parser.parse_args(argv)

    email = args.email.strip().lower()
    if "@" not in email:
        print("error: --email must be a valid email address", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        restaurant = (
            db.execute(
                select(Restaurant).where(Restaurant.slug == args.restaurant_slug)
            )
            .scalars()
            .first()
        )
        if restaurant is None:
            print(
                f"error: no restaurant with slug '{args.restaurant_slug}'",
                file=sys.stderr,
            )
            return 2
        existing_owner = (
            db.execute(
                select(User).where(
                    User.restaurant_id == restaurant.id, User.role == "owner"
                )
            )
            .scalars()
            .first()
        )
        if existing_owner is not None:
            print(
                "error: an owner already exists for this restaurant — "
                "refusing to create another via bootstrap",
                file=sys.stderr,
            )
            return 2
        existing_email = (
            db.execute(
                select(User).where(
                    User.restaurant_id == restaurant.id, User.email == email
                )
            )
            .scalars()
            .first()
        )
        if existing_email is not None:
            print(
                f"error: a user with email '{email}' already exists",
                file=sys.stderr,
            )
            return 2

        password = getpass.getpass("Owner password: ")
        confirm = getpass.getpass("Owner password (again): ")
        if password != confirm:
            print("error: passwords do not match", file=sys.stderr)
            return 2
        if len(password) < 8:
            print("error: password must be at least 8 characters", file=sys.stderr)
            return 2

        user = User(
            restaurant_id=restaurant.id,
            branch_id=None,
            email=email,
            password_hash=hash_password(password),
            role="owner",
            is_active=True,
            can_manage_staff=True,
            can_manage_settings=True,
            can_refund=True,
            can_void=True,
            must_change_password=False,
        )
        db.add(user)
        db.flush()
        write_audit(
            db,
            restaurant_id=restaurant.id,
            action="user.created",
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            details={"email": email, "role": "owner", "via": "bootstrap"},
        )
        db.commit()
        print(f"Owner {email} created for restaurant '{restaurant.slug}'.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
