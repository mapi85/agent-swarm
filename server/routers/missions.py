"""Missions : l'utilisateur décrit un besoin, le superviseur propose un plan,
l'utilisateur valide, le plan se matérialise en tâches confiées aux agents."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import planner
from ..db import get_db
from ..models import Mission, Task, User
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
    query = select(Mission).order_by(Mission.id.desc())
    if user.role != "admin":
        query = query.where(Mission.owner_user_id == user.id)
    if not include_archived:
        query = query.where(Mission.status != "archived")
    return (await db.execute(query)).scalars().all()


@router.get("/{mission_id}", response_model=MissionOut)
async def get_mission(mission_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _get_visible(db, user, mission_id)


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
