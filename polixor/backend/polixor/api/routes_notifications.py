"""התראות: רשימה מקובצת, סימון כנקראו, מחיקה."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import notifications

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class IdsIn(BaseModel):
    ids: Optional[list[int]] = Field(default=None, max_length=500)   # None = הכול


@router.get("")
def list_notifications(limit: int = 60, unread: bool = False) -> dict:
    return notifications.listing(limit=limit, unread_only=unread)


@router.get("/summary")
def summary() -> dict:
    data = notifications.listing(limit=1)
    return {"unread": data["unread"], "needs_attention": data["needs_attention"]}


@router.post("/read")
def mark_read(body: IdsIn) -> dict:
    return {"updated": notifications.mark_read(body.ids)}


@router.post("/delete")
def delete(body: IdsIn, read_only: bool = False) -> dict:
    return {"deleted": notifications.delete(body.ids, read_only=read_only)}
