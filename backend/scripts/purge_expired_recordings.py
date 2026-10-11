"""Delete interview RECORDINGS older than DATA_RETENTION_DAYS (the period promised in the consent form).

    python -m scripts.purge_expired_recordings            # DRY RUN: only lists what would be deleted (default)
    python -m scripts.purge_expired_recordings --apply    # really deletes the video files and clears the DB pointer

Only the video file is removed. Transcripts, scores and reports stay (they are small and are removed when the
candidate deletes the interview). Nothing runs this automatically: run it yourself, or schedule it as a Cloud Run job.
"""
import argparse
from datetime import datetime, timedelta

from sqlalchemy import func

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Interview
from app.services import storage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    parser.add_argument("--days", type=int, default=settings.DATA_RETENTION_DAYS)
    args = parser.parse_args()
    cutoff = datetime.utcnow() - timedelta(days=args.days)
    db = SessionLocal()
    try:
        rows = (db.query(Interview)
                .filter(Interview.video_path.isnot(None))
                .filter(func.coalesce(Interview.video_uploaded_at, Interview.created_at) < cutoff).all())
        print(f"{len(rows)} recording(s) older than {args.days} days ({'DELETING' if args.apply else 'dry run'})")
        for i in rows:
            print(f"  interview {i.id}: {i.video_size_bytes or 0} bytes, uploaded {i.video_uploaded_at}")
            if args.apply:
                try:
                    storage.delete_file(i.video_path)
                except Exception as exc:        # keep going; the pointer stays so it can be retried
                    print(f"    could not delete: {type(exc).__name__}")
                    continue
                i.video_path, i.video_size_bytes = None, None
                db.commit()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
