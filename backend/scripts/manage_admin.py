"""Manage admin logins.

  python -m scripts.manage_admin --email old@x.com --deactivate          (disable a login; history is kept)
  python -m scripts.manage_admin --email me@x.com --reset-password       (new password from ADMIN_PASSWORD)
"""
import argparse
import getpass
import os
import sys

from app.db.database import SessionLocal
from app.services.admin_service import deactivate_admin, reset_admin_password


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--deactivate", action="store_true")
    action.add_argument("--reset-password", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        if args.deactivate:
            admin = deactivate_admin(db, args.email)
            print(f"Deactivated {admin.email}")
        else:
            password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("New password (10-72 chars): ")
            admin = reset_admin_password(db, args.email, password)
            print(f"Password reset for {admin.email}")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
