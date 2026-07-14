"""Missions : l'utilisateur décrit un besoin, le superviseur propose un plan,
l'utilisateur valide, le plan se matérialise en tâches confiées aux agents."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import planner
from ..db import get_db
from ..models import Agent, Mission, Session, Task, User
from ..schemas import MissionCreateIn, MissionOut
from ..security import ensure_owner, get_current_user

log = logging.getLogger("swarm.missions")
router = APIRouter(prefix="/api/missions", tags=["missions"])


async def _get_visible(db: AsyncSession, user: User, mission_id: int) -> Mission:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")
    ensure_owner(user, mission.owner_user_id)
    return mission


@router.get("", response_model=list[MissionOut])
async def list_missions(
    include_archived: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Mission).where(Mission.owner_user_id == user.id).order_by(Mission.id.desc())
    if not include_archived:
        query = query.where(Mission.status != "archived")
    return (await db.execute(query)).scalars().all()


@router.get("/{mission_id}", response_model=MissionOut)
async def get_mission(mission_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _get_visible(db, user, mission_id)


@router.get("/{mission_id}/tasks")
async def mission_tasks(
    mission_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Tâches rattachées à la mission + avancement + sessions (activité réelle).
    En exécution solo, la mission tient en une tâche du superviseur parcourue
    sur plusieurs sessions : les sessions constituent le vrai suivi d'avancement."""
    await _get_visible(db, user, mission_id)
    rows = (
        await db.execute(
            select(Task, Agent).join(Agent, Agent.id == Task.agent_id)
            .where(Task.mission_id == mission_id).order_by(Task.id)
        )
    ).all()
    tids = [t.id for (t, _a) in rows]
    nxt: dict = {}
    if tids:
        r = await db.execute(
            select(Session.task_id, func.min(Session.scheduled_at))
            .where(Session.task_id.in_(tids), Session.status == "planned")
            .group_by(Session.task_id)
        )
        nxt = {tid: at for tid, at in r}
    tasks = [
        {"id": t.id, "title": t.title or t.description[:80], "status": t.status,
         "agent_id": a.id, "agent_name": a.name, "next_session_at": nxt.get(t.id),
         "created_by": t.created_by}
        for (t, a) in rows
    ]

    # Sessions de la mission (l'activité du/des agent(s)) — les plus récentes d'abord.
    sessions = []
    if tids:
        srows = (
            await db.execute(
                select(Session).where(Session.task_id.in_(tids)).order_by(Session.number.desc()).limit(15)
            )
        ).scalars().all()
        for s in srows:
            sessions.append({
                "id": s.id, "number": s.number, "status": s.status,
                "objective": (s.objective or "")[:160],
                "report": (s.report or "")[:240], "ended_at": s.ended_at,
            })

    def count(st: str) -> int:
        return sum(1 for (t, _a) in rows if t.status == st)

    progress = {
        "total": len(rows),
        "done": count("done"),
        "in_progress": count("in_progress"),
        "waiting": count("waiting_user") + count("stalled"),
        "failed": count("failed"),
        "sessions": len(sessions),
    }
    return {"progress": progress, "tasks": tasks, "sessions": sessions}


@router.post("", response_model=MissionOut, status_code=201)
async def create_mission(
    body: MissionCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        plan = await planner.make_plan(db, body.mission, user)
    except Exception as exc:
        log.warning("échec de planification", extra={"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"Le superviseur n'a pas pu produire de plan : {exc}")
    mission = Mission(owner_user_id=user.id, title=plan["title"], mission=body.mission,
                      summary=plan["summary"], plan=plan, status="proposed")
    db.add(mission)
    await db.commit()
    return mission


@router.post("/{mission_id}/replan", response_model=MissionOut)
async def replan(mission_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    mission = await _get_visible(db, user, mission_id)
    if mission.status != "proposed":
        raise HTTPException(status_code=400, detail="Seule une mission encore proposée peut être régénérée")
    plan = await planner.make_plan(db, mission.mission, user)
    mission.title, mission.summary, mission.plan = plan["title"], plan["summary"], plan
    await db.commit()
    return mission


@router.post("/{mission_id}/approve")
async def approve(mission_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    mission = await _get_visible(db, user, mission_id)
    if mission.status != "proposed":
        raise HTTPException(status_code=400, detail="Mission déjà validée")
    result = await planner.materialize(db, mission, user)
    return {"status": "running", **result}


@router.post("/{mission_id}/archive", response_model=MissionOut)
async def archive(mission_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    mission = await _get_visible(db, user, mission_id)
    mission.status = "archived"
    await db.commit()
    return mission


@router.delete("/{mission_id}", status_code=204)
async def delete_mission(mission_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    mission = await _get_visible(db, user, mission_id)
    running = (
        await db.execute(
            select(Task).where(Task.mission_id == mission_id, Task.status == "in_progress").limit(1)
        )
    ).scalar_one_or_none()
    if running is not None:
        raise HTTPException(status_code=409, detail="Une tâche de la mission s'exécute : réessaie plus tard")
    # Les tâches liées sont conservées mais détachées de la mission (historique agent intègre)
    for task in (await db.execute(select(Task).where(Task.mission_id == mission_id))).scalars():
        task.mission_id = None
    await db.delete(mission)
    await db.commit()
