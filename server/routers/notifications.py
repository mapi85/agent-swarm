"""Notifications de l'utilisateur (feed, réponse aux questions, alerte lue).

Répondre à une question relance la tâche concernée : la réponse est injectée
dans le contexte de la session de reprise (voir scheduler/runtime). Le dispatch
vers les canaux externes (email/Telegram) arrive au chantier 5."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Notification, Session, Task, User
from ..security import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class AnswerIn(BaseModel):
    response: str


class NotificationOut(BaseModel):
    id: int
    agent_id: int | None
    task_id: int | None
    session_id: int | None
    type: str
    status: str
    content: str
    response: str | None
    created_at: datetime
    answered_at: datetime | None

    class Config:
        from_attributes = True


async def _get_own(db: AsyncSession, user: User, notif_id: int) -> Notification:
    notif = await db.get(Notification, notif_id)
    if notif is None or (user.role != "admin" and notif.user_id != user.id):
        raise HTTPException(status_code=404, detail="Notification introuvable")
    return notif


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    status: str | None = None,
    type: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc())
    if status:
        query = query.where(Notification.status == status)
    if type:
        query = query.where(Notification.type == type)
    return (await db.execute(query.limit(200))).scalars().all()


@router.post("/{notif_id}/answer", response_model=NotificationOut)
async def answer(notif_id: int, body: AnswerIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    notif = await _get_own(db, user, notif_id)
    if notif.type != "question":
        raise HTTPException(status_code=400, detail="Cette notification n'est pas une question")
    if notif.status == "answered":
        raise HTTPException(status_code=400, detail="Question déjà répondue")
    notif.status = "answered"
    notif.response = body.response
    notif.answered_at = datetime.now(timezone.utc)

    # Relance de la tâche : session de reprise portant la réponse dans son objectif.
    if notif.task_id:
        task = await db.get(Task, notif.task_id)
        if task and task.status in ("waiting_user", "pending", "ready"):
            number = (
                await db.execute(
                    select(func.coalesce(func.max(Session.number), 0)).where(Session.task_id == task.id)
                )
            ).scalar_one() + 1
            db.add(Session(
                task_id=task.id, agent_id=task.agent_id, number=number, status="planned",
                scheduled_at=datetime.now(timezone.utc),
                objective=(f"Reprendre la tâche à la lumière de la réponse de l'utilisateur.\n"
                           f"Question posée : {notif.content}\nRéponse : {body.response}"),
            ))
            task.status = "pending"
    await db.commit()
    return notif


@router.post("/{notif_id}/dismiss", response_model=NotificationOut)
async def dismiss(notif_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    notif = await _get_own(db, user, notif_id)
    notif.status = "dismissed"
    await db.commit()
    return notif
