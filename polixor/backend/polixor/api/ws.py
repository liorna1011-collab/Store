"""WebSocket להתקדמות בזמן אמת."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import BUS
from ..worker import MANAGER

log = logging.getLogger("polixor.api.ws")
router = APIRouter()

HEARTBEAT_SECONDS = 20.0


@router.websocket("/ws")
async def progress_socket(websocket: WebSocket) -> None:
    """
    משדר אירועי התקדמות לכל המשימות.
    הלקוח יכול לסנן לפי job_id; אנחנו לא מנהלים מנויים פר-משימה
    כדי לשמור על פשטות ועמידות בחיבורים שנופלים.
    """
    await websocket.accept()
    loop, queue = BUS.subscribe()

    try:
        await websocket.send_json({
            "type": "hello",
            "data": {"active_jobs": MANAGER.active_ids()},
        })
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "data": {}})
                continue
            await websocket.send_json(event.to_dict())
    except WebSocketDisconnect:
        pass
    except (RuntimeError, ConnectionError) as exc:
        log.debug("websocket closed: %s", exc)
    finally:
        BUS.unsubscribe(loop, queue)
