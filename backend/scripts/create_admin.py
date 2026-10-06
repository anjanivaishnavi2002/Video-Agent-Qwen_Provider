"""Create an admin / recruiter account:   python -m scripts.create_admin --email you@company.com [--role admin]

The password is read from the ADMIN_PASSWORD environment variable or prompted (never passed on the command line,
so it does not end up in shell history or process lists)."""
import argparse
import getpass
import os
import sys

from app.db.database import SessionLocal
from app.services.admin_service import create_admin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--role", default="admin", choices=["admin", "recruiter"])
    args = parser.parse_args()
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password (10-72 chars): ")
    db = SessionLocal()
    try:
        admin = create_admin(db, args.email, password, full_name=args.name, role=args.role)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"Created {admin.role} {admin.email} (id {admin.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
