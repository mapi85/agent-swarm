"""Sessions : supervision de l'exécution d'une tâche (flux d'événements, run-now,
interruption, relance). Une session appartient à une tâche, donc à un utilisateur."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import runtime
from ..db import get_db
from ..models import Event, Session, Task, User
from ..schemas import EventOut, RunNowIn, SessionOut
from ..security import ensure_owner, get_current_user

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


async def _get_visible(db: AsyncSession, user: User, session_id: int) -> tuple[Session, Task]:
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable")
    task = await db.get(Task, session.task_id)
    ensure_owner(user, task.owner_user_id if task else None)
    return session, task


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    task_id: int | None = None,
    agent_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Session).join(Task, Task.id == Session.task_id) \
        .where(Task.owner_user_id == user.id).order_by(Session.id.desc())
    if task_id:
        query = query.where(Session.task_id == task_id)
    if agent_id:
        query = query.where(Session.agent_id == agent_id)
    return (await db.execute(query.limit(200))).scalars().all()


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session, _ = await _get_visible(db, user, session_id)
    return session


@router.get("/{session_id}/events", response_model=list[EventOut])
async def get_events(
    session_id: int,
    after: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_visible(db, user, session_id)
    events = (
        await db.execute(
            select(Event).where(Event.session_id == session_id, Event.id > after)
            .order_by(Event.id).limit(500)
        )
    ).scalars().all()
    return events


@router.post("/{session_id}/run-now", response_model=SessionOut)
async def run_now(
    session_id: int,
    body: RunNowIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session, _ = await _get_visible(db, user, session_id)
    if session.status != "planned":
        raise HTTPException(status_code=400, detail="Seule une session planifiée peut être lancée")
    session.scheduled_at = datetime.now(timezone.utc)
    if body.user_note:
        session.user_note = body.user_note
    await db.commit()
    return session  # le prochain tick du scheduler la lancera


@router.post("/{session_id}/interrupt", response_model=SessionOut)
async def interrupt(session_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session, task = await _get_visible(db, user, session_id)
    if session.status == "running":
        runtime.cancel_running(session_id)
    elif session.status == "planned":
        session.status = "interrupted"
        session.ended_at = datetime.now(timezone.utc)
        if task and task.status in ("pending", "ready"):
            task.status = "pending"
        await db.commit()
    else:
        raise HTTPException(status_code=400, detail="Session déjà terminée")
    return session


@router.post("/{session_id}/retry", response_model=SessionOut)
async def retry(session_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session, task = await _get_visible(db, user, session_id)
    if session.status not in ("failed", "interrupted"):
        raise HTTPException(status_code=400, detail="Seule une session échouée ou interrompue peut être relancée")
    if task is None:
        raise HTTPException(status_code=400, detail="Tâche associée introuvable")
    number = (
        await db.execute(select(func.coalesce(func.max(Session.number), 0)).where(Session.task_id == task.id))
    ).scalar_one() + 1
    new_session = Session(task_id=task.id, agent_id=session.agent_id, number=number,
                          objective=session.objective, status="planned",
                          scheduled_at=datetime.now(timezone.utc))
    db.add(new_session)
    if task.status in ("failed", "cancelled", "waiting_user"):
        task.status = "pending"
    await db.commit()
    await db.refresh(new_session)
    return new_session
