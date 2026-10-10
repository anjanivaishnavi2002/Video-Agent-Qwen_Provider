"""Create a few sample BPO jobs (idempotent). Run: python -m scripts.seed_sample_jobs"""
from app.db.database import SessionLocal, init_db
from app.services.sample_jobs import SAMPLES, add_sample_jobs


def main() -> None:
    init_db()
    with SessionLocal() as db:
        added = add_sample_jobs(db)
    print(f"Added {added} sample job(s); {len(SAMPLES) - added} already existed.")


if __name__ == "__main__":
    main()
