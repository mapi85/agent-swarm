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
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from . import notify
from .config import get_settings
from .db import SessionLocal
from .models import Agent, Event, Session, Task, TaskLink, User
from .runtime import run_session

log = logging.getLogger("swarm.scheduler")

_ACTIVE_SESSION_STATES = ("planned", "running")
_HEARTBEAT_TITLE = "🔁 Veille périodique"


async def _task_has_active_session(db, task_id: int) -> bool:
    return bool(
        (
            await db.execute(
                select(func.count()).select_from(Session)
                .where(Session.task_id == task_id, Session.status.in_(_ACTIVE_SESSION_STATES))
            )
        ).scalar_one()
    )


async def _heartbeat_task(db, agent: Agent) -> Task:
    """Tâche de veille persistante d'un agent à cadence : les sessions récurrentes
    s'y rattachent (une seule tâche, plusieurs sessions numérotées)."""
    task = (
        await db.execute(
            select(Task).where(Task.agent_id == agent.id, Task.title == _HEARTBEAT_TITLE)
        )
    ).scalar_one_or_none()
    if task is not None:
        return task
    owner = agent.owner_user_id
    if owner is None:  # agent système → rattaché à l'admin
        owner = (
            await db.execute(select(func.min(User.id)).where(User.role == "admin"))
        ).scalar()
    task = Task(
        agent_id=agent.id, owner_user_id=owner, title=_HEARTBEAT_TITLE,
        description="Veille périodique : sessions récurrentes déclenchées par la cadence de l'agent. "
                    "Chaque session vérifie l'état courant (file d'ordres, marché, messages…) "
                    "et agit si nécessaire.",
        status="ready", created_by="self",
    )
    db.add(task)
    await db.flush()
    (get_settings().tasks_dir / str(task.id)).mkdir(parents=True, exist_ok=True)
    return task


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
            if await _task_has_active_session(db, task.id):
                continue  # une session est déjà ouverte pour cette tâche (ex. continuation)
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

        # 1b. Cadence de veille : un agent à heartbeat_minutes > 0, inactif et dont
        # l'échéance est due se voit programmer une session immédiate. Garantit qu'un
        # agent récurrent/événementiel ne reste pas dormant faute de tâche en attente.
        now = datetime.now(timezone.utc)
        hb_agents = (
            await db.execute(
                select(Agent).where(Agent.heartbeat_minutes > 0, Agent.paused.is_(False))
            )
        ).scalars().all()
        for agent in hb_agents:
            if await _agent_active_sessions(db, agent.id):
                continue  # occupé (tâche réelle ou veille déjà en cours) → pas de doublon
            last_end = (
                await db.execute(
                    select(func.max(Session.ended_at)).where(Session.agent_id == agent.id)
                )
            ).scalar()
            if last_end is not None and now - last_end < timedelta(minutes=agent.heartbeat_minutes):
                continue  # cadence pas encore échue
            task = await _heartbeat_task(db, agent)
            number = (
                await db.execute(
                    select(func.coalesce(func.max(Session.number), 0)).where(Session.task_id == task.id)
                )
            ).scalar_one() + 1
            db.add(Session(
                task_id=task.id, agent_id=agent.id, number=number, status="planned",
                scheduled_at=now,
                objective="Cycle de veille : commence par LIRE l'état réel (fichiers d'état partagé, "
                          "messages, données de marché à jour via tes outils) — n'utilise jamais un "
                          "résultat ou un fichier d'une tâche/session passée comme s'il était actuel. "
                          "Agis seulement si l'état lu le justifie. Si tu affirmes avoir agi (ordre "
                          "envoyé, message transmis, fichier partagé mis à jour), ce doit correspondre "
                          "à un appel d'outil réellement effectué dans CETTE session — jamais une "
                          "action supposée ou déduite. En l'absence de changement d'état, dis-le "
                          "explicitement plutôt que d'inventer une action. Puis clos la session.",
            ))
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

    # Dispatch des notifications vers les canaux externes (email/Telegram)
    await notify.dispatch_pending()


async def purge_old_events() -> int:
    """Supprime les événements des sessions terminées au-delà de la rétention."""
    days = get_settings().event_retention_days
    if not days:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with SessionLocal() as db:
        old_sessions = (
            await db.execute(
                select(Session.id).where(Session.status.in_(("completed", "failed", "interrupted")),
                                         Session.ended_at < cutoff)
            )
        ).scalars().all()
        if not old_sessions:
            return 0
        result = await db.execute(delete(Event).where(Event.session_id.in_(old_sessions)))
        await db.commit()
        return result.rowcount or 0


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    log.info("planificateur démarré", extra={"interval_s": settings.scheduler_interval_s})
    ticks = 0
    purge_period = max(1, 86400 // max(settings.scheduler_interval_s, 1))  # ~1×/jour
    while not stop_event.is_set():
        try:
            await _tick()
            if ticks % purge_period == 0:
                purged = await purge_old_events()
                if purged:
                    log.info("purge des événements anciens", extra={"deleted": purged})
        except Exception:
            log.exception("erreur dans le tick du planificateur")
        ticks += 1
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
