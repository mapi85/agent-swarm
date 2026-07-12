"""Flux temps réel (SSE) : événements d'une session et compteurs de synthèse.

Le polling est déplacé côté serveur : une seule connexion par flux, la boucle
interroge la base et pousse les nouveautés. Auth par en-tête Bearer (le client
Vue consomme via fetch + ReadableStream, pas via EventSource natif — pas de
token en URL). Le flux se termine quand la session est close.
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from ..db import SessionLocal
from ..models import Event, Notification, Session, Task, User
from ..security import get_current_user

router = APIRouter(prefix="/api/stream", tags=["stream"])

_TERMINAL = ("completed", "failed", "interrupted")


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/sessions/{session_id}/events")
async def stream_session_events(
    session_id: int,
    after: int = 0,
    request: Request = None,
    user: User = Depends(get_current_user),
):
    # Contrôle d'accès (ownership) avant d'ouvrir le flux
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session introuvable")
        task = await db.get(Task, session.task_id)
        if user.role != "admin" and (task is None or task.owner_user_id != user.id):
            raise HTTPException(status_code=404, detail="Session introuvable")

    async def gen():
        last_id = after
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            async with SessionLocal() as db:
                events = (
                    await db.execute(
                        select(Event).where(Event.session_id == session_id, Event.id > last_id)
                        .order_by(Event.id).limit(200)
                    )
                ).scalars().all()
                for e in events:
                    last_id = e.id
                    yield _sse("event", {"id": e.id, "type": e.type, "ts": e.ts.isoformat(),
                                         "content": e.content})
                status = (
                    await db.execute(select(Session.status).where(Session.id == session_id))
                ).scalar_one_or_none()
            if events:
                idle = 0
            else:
                idle += 1
            if status in _TERMINAL:
                yield _sse("end", {"status": status})
                break
            yield ": keep-alive\n\n" if idle % 15 == 0 else ""
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/overview")
async def stream_overview(request: Request, user: User = Depends(get_current_user)):
    """Compteurs de synthèse poussés quand ils changent (agents, tâches ouvertes,
    sessions actives, notifications ouvertes) — pour l'utilisateur courant."""
    async def counts(db) -> dict:
        scope_task = Task.owner_user_id == user.id
        open_tasks = (
            await db.execute(
                select(func.count()).select_from(Task).where(
                    Task.status.in_(("pending", "ready", "in_progress", "waiting_user", "stalled")),
                    *([] if user.role == "admin" else [scope_task]),
                )
            )
        ).scalar_one()
        running = (
            await db.execute(
                select(func.count()).select_from(Session).join(Task, Task.id == Session.task_id)
                .where(Session.status == "running", *([] if user.role == "admin" else [scope_task]))
            )
        ).scalar_one()
        open_notifs = (
            await db.execute(
                select(func.count()).select_from(Notification).where(
                    Notification.user_id == user.id, Notification.status == "open")
            )
        ).scalar_one()
        return {"open_tasks": open_tasks, "running_sessions": running, "open_notifications": open_notifs}

    async def gen():
        previous = None
        while True:
            if await request.is_disconnected():
                break
            async with SessionLocal() as db:
                current = await counts(db)
            if current != previous:
                yield _sse("overview", current)
                previous = current
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
