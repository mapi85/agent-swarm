"""Tâches : unité de travail ET de supervision (REFONTE.md §4).

Les liens entre tâches (task_links) portent les dépendances de mission et la
porosité : une tâche accède en lecture à toute sa chaîne d'ascendance
(fermeture transitive, cycles refusés)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Agent, Notification, Session, Task, TaskLink, User
from ..routers_common import ancestor_ids
from ..schemas import TaskCreateIn, TaskDetailOut, TaskLinkIn, TaskLinkOut, TaskOut, TaskPatchIn, TaskRelanceIn
from ..security import ensure_owner, get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


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
    query = select(Task).where(Task.owner_user_id == user.id).order_by(Task.id.desc())
    if status:
        query = query.where(Task.status == status)
    if agent_id:
        query = query.where(Task.agent_id == agent_id)
    if mission_id:
        query = query.where(Task.mission_id == mission_id)
    tasks = (await db.execute(query)).scalars().all()
    # Prochaine session planifiée par tâche (une requête groupée, pas de N+1)
    ids = [t.id for t in tasks]
    nxt: dict = {}
    blocked: dict[int, list] = {}
    if ids:
        rows = await db.execute(
            select(Session.task_id, func.min(Session.scheduled_at))
            .where(Session.task_id.in_(ids), Session.status == "planned")
            .group_by(Session.task_id)
        )
        nxt = {tid: at for tid, at in rows}
        # Dépendances depends_on non terminées (ce que chaque tâche attend)
        blk_rows = (
            await db.execute(
                select(TaskLink.task_id, Task.id, Task.title, Task.status, Task.agent_id)
                .join(Task, Task.id == TaskLink.linked_task_id)
                .where(TaskLink.task_id.in_(ids), TaskLink.kind == "depends_on",
                       Task.status != "done")
            )
        ).all()
        for tid, lid, ltitle, lstatus, lagent in blk_rows:
            blocked.setdefault(tid, []).append(
                TaskLinkOut(task_id=lid, title=ltitle or "", status=lstatus, agent_id=lagent,
                            kind="depends_on")
            )
    out = []
    for t in tasks:
        o = TaskOut.model_validate(t)
        o.next_session_at = nxt.get(t.id)
        o.blocked_by = blocked.get(t.id, [])
        out.append(o)
    return out


@router.get("/attention")
async def attention(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Bac « À traiter » : tâches qui nécessitent l'utilisateur.
    - questions : tâches en 'waiting_user' avec leur notification question ouverte
    - stalled   : tâches bloquées (auto-continuations sans progrès)
    (Déclarée avant /{task_id} pour ne pas être capturée par la route paramétrée.)"""
    scope = [Task.owner_user_id == user.id]  # comptes indépendants (admin compris)

    qrows = (
        await db.execute(
            select(Task, Notification, Agent)
            .join(Notification, Notification.task_id == Task.id)
            .join(Agent, Agent.id == Task.agent_id)
            .where(
                Task.status == "waiting_user",
                Notification.type == "question", Notification.status == "open",
                *scope,
            ).order_by(Notification.id.desc())
        )
    ).all()
    questions = [
        {"notif_id": n.id, "task_id": t.id, "title": t.title or t.description[:80],
         "agent_id": a.id, "agent_name": a.name, "content": n.content}
        for (t, n, a) in qrows
    ]

    srows = (
        await db.execute(
            select(Task, Agent).join(Agent, Agent.id == Task.agent_id)
            .where(Task.status == "stalled", *scope).order_by(Task.id.desc())
        )
    ).all()
    stalled = [
        {"task_id": t.id, "title": t.title or t.description[:80],
         "agent_id": a.id, "agent_name": a.name, "agent_paused": a.paused,
         "consecutive_stalls": t.consecutive_stalls}
        for (t, a) in srows
    ]
    return {"questions": questions, "stalled": stalled}


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
    if agent is None or agent.owner_user_id not in (None, user.id):
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
    if task.status not in ("pending", "ready", "waiting_user", "stalled"):
        raise HTTPException(status_code=400, detail="Seule une tâche non démarrée peut être annulée")
    task.status = "cancelled"
    # Solder aussi les sessions planifiées : sinon elles restent orphelines et
    # partiraient quand même à la prochaine reprise de l'agent.
    planned = (
        await db.execute(
            select(Session).where(Session.task_id == task_id, Session.status == "planned")
        )
    ).scalars().all()
    for s in planned:
        s.status = "interrupted"
        s.ended_at = datetime.now(timezone.utc)
        s.error = "tâche annulée"
    await db.commit()
    return task


@router.post("/{task_id}/relance", response_model=TaskOut)
async def relance_task(
    task_id: int,
    body: TaskRelanceIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Relance manuelle d'une tâche : crée une session planifiée immédiate.
    `body.note` devient le `user_note` de la session (injecté dans le prompt de
    l'agent), pour préciser ce qu'il faut faire aux prochaines sessions."""
    task = await _get_visible(db, user, task_id)
    if task.status in ("done", "cancelled"):
        raise HTTPException(status_code=400, detail="Une tâche terminée ne se relance pas")
    # Agent en pause : la session ne partirait jamais (trompeur). On exige un choix
    # explicite : resume_agent=true pour réactiver l'agent avec la relance.
    agent = await db.get(Agent, task.agent_id)
    if agent and agent.paused:
        if not body.resume_agent:
            raise HTTPException(
                status_code=409,
                detail=f"L'agent « {agent.name} » est en pause : la session ne partirait pas. "
                       "Relance avec resume_agent=true pour le réactiver, ou reprends-le d'abord.",
            )
        if agent.owner_user_id is None and user.role != "admin":
            raise HTTPException(status_code=403,
                                detail="Cet agent système est en pause (réactivation réservée à l'administrateur)")
        agent.paused = False
    last_obj = (
        await db.execute(
            select(Session.objective).where(Session.task_id == task_id)
            .order_by(Session.number.desc()).limit(1)
        )
    ).scalar()
    objective = f"Reprise manuelle de la tâche #{task_id} : {task.title or task.description[:80]}"
    if last_obj:
        objective += f"\nObjectif précédent : {last_obj}"
    number = (
        await db.execute(
            select(func.coalesce(func.max(Session.number), 0)).where(Session.task_id == task_id)
        )
    ).scalar_one() + 1
    db.add(Session(
        task_id=task_id, agent_id=task.agent_id, number=number, status="planned",
        scheduled_at=datetime.now(timezone.utc), objective=objective, user_note=body.note,
    ))
    task.status = "pending"
    task.consecutive_stalls = 0
    await db.commit()
    out = TaskOut.model_validate(task)
    out.next_session_at = datetime.now(timezone.utc)
    return out


@router.patch("/{task_id}", response_model=TaskDetailOut)
async def patch_task(
    task_id: int,
    body: TaskPatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Réorientation d'une tâche : modifie sa spécification de travail (titre,
    description). Ne change pas le statut ni l'agent."""
    task = await _get_visible(db, user, task_id)
    ensure_owner(user, task.owner_user_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Rien à modifier")
    for key, value in fields.items():
        setattr(task, key, value)
    await db.commit()
    out = TaskDetailOut.model_validate(task)
    out.antecedents, out.dependents = await _links_out(db, task_id)
    return out
