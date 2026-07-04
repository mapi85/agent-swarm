"""Planificateur : promeut les tâches prêtes, lance les sessions à échéance,
respecte le parallélisme par agent et le plafond global.

Modèle « 1 session = 1 tâche » :
- une tâche `pending` sans session ouverte et dont toutes les dépendances
  `depends_on` sont `done` devient `ready` avec une session immédiate ;
- un agent traite jusqu'à `max_parallel_tasks` sessions en parallèle ;
- une question `ask_user` bloque sa tâche (`waiting_user`), pas l'agent.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from .config import get_settings
from .db import SessionLocal
from .models import Agent, Event, Session, Task, TaskLink
from .runtime import run_session

log = logging.getLogger("swarm.scheduler")

_ACTIVE_SESSION_STATES = ("planned", "running")


async def _dependencies_satisfied(db, task_id: int) -> bool:
    dep_ids = [
        row[0] for row in (
            await db.execute(
                select(TaskLink.linked_task_id).where(
                    TaskLink.task_id == task_id, TaskLink.kind == "depends_on"
                )
            )
        ).all()
    ]
    if not dep_ids:
        return True
    statuses = [
        row[0] for row in (await db.execute(select(Task.status).where(Task.id.in_(dep_ids)))).all()
    ]
    return all(s == "done" for s in statuses)


async def _running_count(db) -> int:
    return (
        await db.execute(select(func.count()).select_from(Session).where(Session.status == "running"))
    ).scalar_one()


async def _agent_active_sessions(db, agent_id: int) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(Session)
            .where(Session.agent_id == agent_id, Session.status.in_(_ACTIVE_SESSION_STATES))
        )
    ).scalar_one()


async def _tick() -> None:
    settings = get_settings()
    to_launch: list[int] = []

    async with SessionLocal() as db:
        global_running = await _running_count(db)
        capacity = settings.max_concurrent_sessions - global_running

        # 1. Promotion des tâches pending → ready + création de session immédiate
        pending = (
            await db.execute(
                select(Task).where(Task.status == "pending").order_by(Task.id)
            )
        ).scalars().all()
        # Nombre de sessions actives par agent (pour le plafond de parallélisme)
        active_by_agent: dict[int, int] = {}
        agents: dict[int, Agent] = {}
        for task in pending:
            agent = agents.get(task.agent_id)
            if agent is None:
                agent = await db.get(Agent, task.agent_id)
                agents[task.agent_id] = agent
                active_by_agent[task.agent_id] = await _agent_active_sessions(db, task.agent_id)
            if agent is None or agent.paused:
                continue
            if active_by_agent[task.agent_id] >= agent.max_parallel_tasks:
                continue
            if not await _dependencies_satisfied(db, task.id):
                continue
            number = (
                await db.execute(
                    select(func.coalesce(func.max(Session.number), 0)).where(Session.task_id == task.id)
                )
            ).scalar_one() + 1
            sess = Session(task_id=task.id, agent_id=task.agent_id, number=number,
                           objective=f"Traiter la tâche #{task.id} : {task.title or task.description[:80]}",
                           status="planned", scheduled_at=datetime.now(timezone.utc))
            db.add(sess)
            task.status = "ready"
            active_by_agent[task.agent_id] += 1
        await db.commit()

        # 2. Sessions planifiées échues → à lancer (dans la limite de capacité)
        now = datetime.now(timezone.utc)
        due = (
            await db.execute(
                select(Session).where(Session.status == "planned", Session.scheduled_at <= now)
                .order_by(Session.scheduled_at)
            )
        ).scalars().all()
        # Sessions déjà en cours par agent (running) : base du plafond de parallélisme.
        running_by_agent: dict[int, int] = {}
        for sess in due:
            if capacity <= 0:
                break
            agent = agents.get(sess.agent_id) or await db.get(Agent, sess.agent_id)
            agents[sess.agent_id] = agent
            if agent is None or agent.paused:
                continue
            if sess.agent_id not in running_by_agent:
                running_by_agent[sess.agent_id] = (
                    await db.execute(
                        select(func.count()).select_from(Session)
                        .where(Session.agent_id == sess.agent_id, Session.status == "running")
                    )
                ).scalar_one()
            if running_by_agent[sess.agent_id] >= agent.max_parallel_tasks:
                continue
            to_launch.append(sess.id)
            running_by_agent[sess.agent_id] += 1  # cette session va démarrer
            capacity -= 1
        # La réservation planned→running est atomique dans run_session : un double
        # lancement éventuel est neutralisé (0 ligne affectée → sortie immédiate).

    # Hors transaction : lancer les sessions retenues
    for sid in to_launch:
        log.info("lancement de session", extra={"session_id": sid})
        asyncio.create_task(run_session(sid))


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    log.info("planificateur démarré", extra={"interval_s": settings.scheduler_interval_s})
    while not stop_event.is_set():
        try:
            await _tick()
        except Exception:
            log.exception("erreur dans le tick du planificateur")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.scheduler_interval_s)
        except asyncio.TimeoutError:
            pass


async def recover_stale_state() -> None:
    """Au démarrage : sessions 'running' orphelines (crash) → échec ;
    leurs tâches 'in_progress' → 'pending' (reprises au prochain tick)."""
    async with SessionLocal() as db:
        stale = (await db.execute(select(Session).where(Session.status == "running"))).scalars().all()
        for s in stale:
            s.status = "failed"
            s.ended_at = datetime.now(timezone.utc)
            s.error = "interrompue par un redémarrage de la plateforme"
            db.add(Event(session_id=s.id, type="error",
                         content="Session interrompue par un redémarrage de la plateforme."))
            task = await db.get(Task, s.task_id)
            if task and task.status == "in_progress":
                task.status = "pending"
        await db.commit()
