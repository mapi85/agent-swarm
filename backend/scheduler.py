"""Planificateur : lance les sessions à échéance et crée des sessions pour les tâches en attente."""
import asyncio
import logging

from . import config, db, runtime

log = logging.getLogger("swarm.scheduler")


async def _tick() -> None:
    # Un agent en attente d'une réponse humaine (question ouverte) ne lance aucune session.
    waiting = db.agents_awaiting()

    # 1. Sessions planifiées arrivées à échéance (agents libres et non en attente)
    for session in db.due_sessions():
        if db.running_sessions_count() >= config.MAX_CONCURRENT_SESSIONS:
            break
        agent = db.get_agent(session["agent_id"])
        if not agent or agent["status"] != "idle" or agent["id"] in waiting:
            continue
        log.info("Lancement session #%s (agent %s)", session["id"], agent["name"])
        asyncio.create_task(runtime.run_session(session["id"]))
        db.set_agent_status(agent["id"], "running")

    # 2. Tâches en attente pour des agents libres sans session planifiée → session immédiate
    for agent in db.list_agents():
        if agent["status"] != "idle" or agent["id"] in waiting:
            continue
        if db.query_one("SELECT id FROM sessions WHERE agent_id = ? AND status IN ('planned','running') LIMIT 1",
                        (agent["id"],)):
            continue
        tasks = db.ready_tasks(agent["id"])
        if not tasks:
            continue
        objective = "Traiter les tâches prêtes qui te sont confiées :\n" + "\n".join(
            f"- Tâche #{t['id']} (de {t['origin']}) : {t['description']}" for t in tasks
        )
        sid = db.create_session(agent["id"], objective, None)
        log.info("Session #%s créée pour les tâches de %s", sid, agent["name"])

    # 3. Réponses de l'utilisateur reçues → reprise de l'agent (s'il n'attend plus aucune réponse)
    for aid in db.agents_with_undelivered_answers():
        if aid in waiting:
            continue  # d'autres questions sont encore en attente
        agent = db.get_agent(aid)
        if not agent or agent["status"] != "idle":
            continue
        if db.query_one("SELECT id FROM sessions WHERE agent_id = ? AND status IN ('planned','running') LIMIT 1",
                        (aid,)):
            continue  # une session planifiée délivrera la réponse
        sid = db.create_session(aid, "Reprendre le travail à la lumière de la ou des réponses de l'utilisateur "
                                     "à tes questions (voir ton contexte initial).", None)
        log.info("Session #%s créée pour traiter les réponses utilisateur de %s", sid, agent["name"])


async def scheduler_loop() -> None:
    log.info("Planificateur démarré (intervalle %ss)", config.SCHEDULER_INTERVAL_S)
    while True:
        try:
            await _tick()
        except Exception:
            log.exception("Erreur dans le tick du planificateur")
        await asyncio.sleep(config.SCHEDULER_INTERVAL_S)


def recover_stale_state() -> None:
    """Au démarrage : les sessions 'running' orphelines (crash/redémarrage) repassent en échec."""
    stale = db.query("SELECT id, agent_id FROM sessions WHERE status = 'running'")
    for s in stale:
        db.update_session(s["id"], status="failed", ended_at=db.now(),
                          error="interrompue par un redémarrage de la plateforme")
        db.add_event(s["id"], s["agent_id"], "error",
                     "Session interrompue par un redémarrage de la plateforme.")
    db.execute("UPDATE agents SET status = 'idle' WHERE status = 'running'")
