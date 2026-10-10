"""Test environment: set BEFORE the application settings are imported (settings are read once)."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ.setdefault("UPLOAD_DIR", f"{_tmp}/uploads")
os.environ.setdefault("AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123456")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("INTERVIEW_MODE", "turn")        # live-mode tests switch it on explicitly
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "")
os.environ.setdefault("NOTIFICATION_SERVICE_URL", "")
os.environ.setdefault("LOGIN_MAX_ATTEMPTS", "5")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")      # lets tests read the full reviewer view
os.environ.setdefault("REQUIRE_UNLOCK", "false")        # unlock behaviour is tested explicitly in test_portal.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
