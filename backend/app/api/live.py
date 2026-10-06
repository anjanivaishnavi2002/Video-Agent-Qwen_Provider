"""WebSocket endpoint for the Gemini Live interview. Protocol: see app/services/live_service.py."""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Interview
from app.security import _check_token
from app.services import live_service, session_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["live"])


class _Socket:
    """Adapts a Starlette WebSocket to the small interface LiveInterview uses."""

    def __init__(self, ws: WebSocket):
        self.ws = ws

    async def receive(self):
        try:
            message = await self.ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            return None
        if message.get("type") == "websocket.disconnect":
            return None
        return message

    async def send_bytes(self, data: bytes):
        if self.ws.application_state == WebSocketState.CONNECTED:
            await self.ws.send_bytes(data)

    async def send_json(self, data: dict):
        if self.ws.application_state == WebSocketState.CONNECTED:
            await self.ws.send_json(data)


def _origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:                       # non-browser clients (tests, tools) - the session token still protects it
        return True
    return origin in settings.cors_origins_list


@router.websocket("/session/{session_id}/live")
async def live_interview(ws: WebSocket, session_id: int):
    if not settings.live_mode or not _origin_allowed(ws):
        await ws.close(code=4403)
        return
    await ws.accept()
    sock = _Socket(ws)

    # The browser cannot set headers on a WebSocket, so the session token is the FIRST message (never in the URL,
    # which would end up in access logs).
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=settings.LIVE_AUTH_TIMEOUT_SECONDS)
    except Exception:
        await ws.close(code=4401)
        return
    db = SessionLocal()
    try:
        ok = first.get("type") == "auth" and _check_token(db, session_id, str(first.get("token") or ""))
        interview = db.get(Interview, session_id) if ok else None
        session = session_manager.get_session(db, session_id) if interview and interview.status == "running" else None
    finally:
        db.close()
    if not session:
        await ws.close(code=4403)
        return

    try:
        await live_service.LiveInterview(session_id, session, sock).run()
    except Exception:
        logger.exception("Live interview socket crashed")
    finally:
        if ws.application_state == WebSocketState.CONNECTED:
            await ws.close()
