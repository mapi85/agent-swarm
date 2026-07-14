"""Synthèse (compteurs) et recherche globale, cadrées au périmètre de l'utilisateur."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Agent, Mission, Notification, Resource, Session, Task, User
from ..security import get_current_user

router = APIRouter(tags=["overview"])


def _scope(query, model_owner, user: User):
    # Comptes indépendants : chaque utilisateur (admin compris) ne voit que ses objets.
    return query.where(model_owner == user.id)


@router.get("/api/overview")
async def overview(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    async def count(stmt):
        return (await db.execute(stmt)).scalar_one()

    agents_q = select(func.count()).select_from(Agent).where(
        (Agent.owner_user_id == user.id) | (Agent.owner_user_id.is_(None)))
    agents = await count(agents_q)
    open_tasks = await count(_scope(
        select(func.count()).select_from(Task).where(
            Task.status.in_(("pending", "ready", "in_progress", "waiting_user", "stalled"))), Task.owner_user_id, user))
    running = await count(
        select(func.count()).select_from(Session).join(Task, Task.id == Session.task_id).where(
            Session.status == "running", Task.owner_user_id == user.id))
    planned = await count(
        select(func.count()).select_from(Session).join(Task, Task.id == Session.task_id).where(
            Session.status == "planned", Task.owner_user_id == user.id))
    missions = await count(_scope(
        select(func.count()).select_from(Mission).where(Mission.status == "running"),
        Mission.owner_user_id, user))
    open_notifs = await count(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user.id, Notification.status == "open"))
    return {"agents": agents, "open_tasks": open_tasks, "running_sessions": running,
            "planned_sessions": planned, "running_missions": missions, "open_notifications": open_notifs}


@router.get("/api/search")
async def search(q: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Recherche globale (tâches, missions, ressources) dans le périmètre de l'utilisateur."""
    like = f"%{q.strip()}%"
    if len(q.strip()) < 2:
        return {"tasks": [], "missions": [], "resources": []}

    tasks_q = (select(Task).where(Task.owner_user_id == user.id,
                 or_(Task.title.ilike(like), Task.description.ilike(like))).limit(20))
    missions_q = (select(Mission).where(Mission.owner_user_id == user.id,
                   or_(Mission.title.ilike(like), Mission.mission.ilike(like))).limit(20))
    res_q = (select(Resource).where(or_(Resource.owner_user_id == user.id, Resource.scope == "shared"),
               or_(Resource.name.ilike(like), Resource.description.ilike(like))).limit(20))

    tasks = (await db.execute(tasks_q)).scalars().all()
    missions = (await db.execute(missions_q)).scalars().all()
    resources = (await db.execute(res_q)).scalars().all()
    return {
        "tasks": [{"id": t.id, "title": t.title or t.description[:60], "status": t.status,
                   "agent_id": t.agent_id} for t in tasks],
        "missions": [{"id": m.id, "title": m.title, "status": m.status} for m in missions],
        "resources": [{"id": r.id, "name": r.name, "kind": r.kind, "scope": r.scope,
                       "task_id": r.task_id} for r in resources],
    }
