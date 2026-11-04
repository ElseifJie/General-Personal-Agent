"""Voice WebSocket for the GPA conversation.

The connection is bound to one ``conversationId``; the same conversation is
used by the text page. Audio goes in as 16 kHz PCM, replies come back as text
deltas and 24 kHz PCM frames. All context/task work stays inside the Personal
Agent and ConversationService.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from general_personal_agent.auth import verify_session
from general_personal_agent.gpa.logging import get_logger
from general_personal_agent.gpa.voice.session import GpaVoiceSession

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/session")
async def voice_ws(websocket: WebSocket) -> None:
    services = websocket.app.state.services
    gpa = getattr(websocket.app.state, "gpa", None)
    settings = services.settings
    if gpa is None:
        await websocket.close(code=4503)
        return

    token = websocket.cookies.get(settings.session_cookie_name)
    payload = verify_session(token, settings.app_secret_key) if token else None
    if not payload:
        await websocket.close(code=4401)
        return
    user = await services.db.get_app_user(payload["sub"])
    if not user or user["status"] != "active":
        await websocket.close(code=4403)
        return

    conversation_id = (
        websocket.query_params.get("conversationId") or websocket.query_params.get("conversation_id") or ""
    ).strip()
    if not conversation_id:
        await websocket.close(code=4400)
        return
    tenant_id = settings.ov_tenant_id or "demo"
    conversation = await gpa.store.get(conversation_id, tenant_id, user["app_user_id"])
    if conversation is None:
        await websocket.close(code=4404)
        return
    if not await gpa.rate_limiter.allow(
        f"voice:{user['app_user_id']}",
        int(settings.gpa_rate_limit_ws_per_min),
    ):
        await websocket.close(code=4429)
        return

    resume_cue = (websocket.query_params.get("resumed") or "").strip().lower() in {
        "1",
        "true",
        "current",
        "task",
        "recent",
    }
    await websocket.accept()
    session = GpaVoiceSession(
        websocket=websocket,
        gpa=gpa,
        conversation=conversation,
        settings=settings,
        tts_http=services.tts(),
        resume_cue=resume_cue,
    )
    if not gpa.voice_sessions.acquire(conversation_id, session):
        await websocket.close(code=4409)
        await session.close()
        return
    try:
        await session.start()
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind == "audio":
                try:
                    chunk = base64.b64decode(message.get("data", ""))
                except Exception:
                    continue
                await session.audio(chunk)
            elif kind == "audio_end":
                await session.audio_end()
            elif kind == "interrupt":
                await session.interrupt()
            elif kind == "text":
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "VOICE_TEXT_UNSUPPORTED",
                        "message": "语音页不接受文本输入；请切换到文本交互页。",
                    }
                )
            elif kind == "ping":
                await websocket.send_json({"type": "pong"})
            elif kind == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("voice websocket failed")
    finally:
        gpa.voice_sessions.release(conversation_id, session)
        await session.close()
