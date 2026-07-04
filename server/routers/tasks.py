"""Tâches : unité de travail ET de supervision (REFONTE.md §4).

Les liens entre tâches (task_links) portent les dépendances de mission et la
porosité : une tâche accède en lecture à toute sa chaîne d'ascendance
(fermeture transitive, cycles refusés)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Agent, Task, TaskLink, User
from ..schemas import TaskCreateIn, TaskDetailOut, TaskLinkIn, TaskLinkOut, TaskOut
from ..security import ensure_owner, get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_ANCESTORS_SQL = text(
    """
    WITH RECURSIVE anc(id) AS (
        SELECT linked_task_id FROM task_links WHERE task_id = :task_id
        UNION
        SELECT tl.linked_task_id FROM task_links tl JOIN anc ON tl.task_id = anc.id
    )
    SELECT id FROM anc
    """
)


async def ancestor_ids(db: AsyncSession, task_id: int) -> set[int]:
    """Fermeture transitive des liens sortants (toute la chaîne d'ascendance)."""
    return {row[0] for row in (await db.execute(_ANCESTORS_SQL, {"task_id": task_id})).all()}


async def _get_visible(db: AsyncSession, user: User, task_id: int) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable")
    ensure_owner(user, task.owner_user_id)
    return task


async def _check_new_link(db: AsyncSession, task: Task, link: TaskLinkIn, user: User) -> None:
    if link.task_id == task.id:
        raise HTTPException(status_code=400, detail="Une tâche ne peut pas être liée à elle-même")
    linked = await _get_visible(db, user, link.task_id)
    if linked.owner_user_id != task.owner_user_id:
        raise HTTPException(status_code=400, detail="Les tâches liées doivent appartenir au même utilisateur")
    if task.id in await ancestor_ids(db, link.task_id):
        raise HTTPException(status_code=400, detail="Ce lien créerait un cycle entre les tâches")


async def _links_out(db: AsyncSession, task_id: int) -> tuple[list[TaskLinkOut], list[TaskLinkOut]]:
    def to_out(link: TaskLink, task: Task) -> TaskLinkOut:
        return TaskLinkOut(
            task_id=task.id, title=task.title or task.description[:80], status=task.status,
            agent_id=task.agent_id, kind=link.kind,
        )

    antecedents = [
        to_out(link, linked)
        for link, linked in (
            await db.execute(
                select(TaskLink, Task).join(Task, Task.id == TaskLink.linked_task_id)
                .where(TaskLink.task_id == task_id)
            )
        ).all()
    ]
    dependents = [
        to_out(link, dependent)
        for link, dependent in (
            await db.execute(
                select(TaskLink, Task).join(Task, Task.id == TaskLink.task_id)
                .where(TaskLink.linked_task_id == task_id)
            )
        ).all()
    ]
    return antecedents, dependents


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    status: str | None = None,
    agent_id: int | None = None,
    mission_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Task).order_by(Task.id.desc())
    if user.role != "admin":
        query = query.where(Task.owner_user_id == user.id)
    if status:
        query = query.where(Task.status == status)
    if agent_id:
        query = query.where(Task.agent_id == agent_id)
    if mission_id:
        query = query.where(Task.mission_id == mission_id)
    return (await db.execute(query)).scalars().all()


@router.get("/{task_id}", response_model=TaskDetailOut)
async def get_task(task_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    task = await _get_visible(db, user, task_id)
    out = TaskDetailOut.model_validate(task)
    out.antecedents, out.dependents = await _links_out(db, task_id)
    return out


@router.get("/{task_id}/ancestors", response_model=list[TaskOut])
async def get_ancestors(
    task_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Toute la chaîne d'ascendance (porosité) — les ressources et artefacts
    de ces tâches sont lisibles depuis celle-ci."""
    await _get_visible(db, user, task_id)
    ids = await ancestor_ids(db, task_id)
    if not ids:
        return []
    return (await db.execute(select(Task).where(Task.id.in_(ids)).order_by(Task.id))).scalars().all()


@router.post("", response_model=TaskDetailOut, status_code=201)
async def create_task(
    body: TaskCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    agent = await db.get(Agent, body.agent_id)
    if agent is None or (user.role != "admin" and agent.owner_user_id not in (None, user.id)):
        raise HTTPException(status_code=404, detail="Agent introuvable")
    task = Task(
        agent_id=agent.id,
        owner_user_id=user.id,
        title=body.title,
        description=body.description,
        created_by="user",
        status="pending",
    )
    db.add(task)
    await db.flush()
    for link in body.links:
        await _check_new_link(db, task, link, user)
        db.add(TaskLink(task_id=task.id, linked_task_id=link.task_id, kind=link.kind))
    await db.commit()
    (get_settings().tasks_dir / str(task.id)).mkdir(parents=True, exist_ok=True)
    out = TaskDetailOut.model_validate(task)
    out.antecedents, out.dependents = await _links_out(db, task.id)
    return out


@router.post("/{task_id}/links", response_model=TaskDetailOut, status_code=201)
async def add_link(
    task_id: int,
    body: TaskLinkIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await _get_visible(db, user, task_id)
    await _check_new_link(db, task, body, user)
    exists = await db.get(TaskLink, (task_id, body.task_id, body.kind))
    if exists is not None:
        raise HTTPException(status_code=409, detail="Ce lien existe déjà")
    db.add(TaskLink(task_id=task_id, linked_task_id=body.task_id, kind=body.kind))
    await db.commit()
    out = TaskDetailOut.model_validate(task)
    out.antecedents, out.dependents = await _links_out(db, task_id)
    return out


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(
    task_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    task = await _get_visible(db, user, task_id)
    if task.status not in ("pending", "ready", "waiting_user"):
        raise HTTPException(status_code=400, detail="Seule une tâche non démarrée peut être annulée")
    task.status = "cancelled"
    await db.commit()
    return task
