"""Admin API (everything under /admin). Each router below is protected by backend dependencies - see app/security.py."""
from fastapi import APIRouter

from app.api.admin import auth, candidates, interviews, jobs, misc

router = APIRouter(prefix="/admin")
router.include_router(auth.router)                  # /admin/auth/login is the only unauthenticated route
router.include_router(misc.dashboard_router)
router.include_router(candidates.router)
router.include_router(jobs.router)
router.include_router(interviews.router)
router.include_router(misc.notifications_router)
router.include_router(misc.users_router)
router.include_router(misc.system_router)
