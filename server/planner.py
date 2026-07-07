"""Superviseur de missions : à partir d'une mission décrite par un utilisateur,
produit un plan décomposé en tâches (parallèles / séquentielles), puis le
matérialise en agents + tâches + liens de dépendance.

Le superviseur est un agent SYSTÈME (owner NULL, nom réservé) : son prompt et
son modèle sont paramétrables par l'admin. Les agents qu'il crée appartiennent
à l'utilisateur propriétaire de la mission (agents dédiés)."""
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import llm
from .config import get_settings
from .llm import block_get, block_type
from .models import Agent, Mission, Provider, Task, TaskLink, User

log = logging.getLogger("swarm.planner")

SUPERVISOR_NAME = "Superviseur"

SUPERVISOR_PROMPT = """Tu es l'agent SUPERVISEUR d'un essaim d'agents autonomes. À partir d'une mission décrite
par un utilisateur, tu produis un PLAN clair : une décomposition en tâches distinctes confiées à des agents,
certaines en parallèle, d'autres séquentielles (une tâche peut dépendre du résultat d'une autre).

Règles :
- Reste simple et lisible : le minimum de tâches nécessaires.
- Confie chaque tâche à un agent existant adapté (par son nom, parmi ceux fournis). Ne propose un NOUVEL agent
  que si aucun agent existant ne convient, en le décrivant précisément (nom, description, mission_prompt).
- Exprime les enchaînements via depends_on (refs locales des tâches prérequises). Pas de dépendance = parallèle.
- Chaque description de tâche doit être autonome et complète (contexte, attendu, critères de réussite).
- Sobriété des NOUVEAUX agents : si tu proposes un new_agent, son mission_prompt doit être sobre en tokens sans perte de qualité — calcul déterministe dans des scripts (pas dans le LLM), prompt concis, sortie structurée quand possible, discipline mémoire (faits en mémoire structurée, MEMORY.md court), effort adapté à la complexité (jamais un modèle moindre, seulement moduler l'effort). La plateforme applique déjà cache, plafond de tokens/session et compaction : conçois pour des sessions bornées avec reprise (finish_session + next_objective).

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour :
{
  "title": "titre court de la mission",
  "summary": "explication du plan en 2-4 phrases",
  "new_agents": [{"name": "...", "description": "...", "mission_prompt": "..."}],
  "tasks": [
    {"ref": "t1", "agent": "nom-agent", "title": "titre court", "description": "description complète", "depends_on": []},
    {"ref": "t2", "agent": "nom-agent", "title": "...", "description": "...", "depends_on": ["t1"]}
  ]
}
new_agents peut être vide. Les refs sont locales au plan (t1, t2, …)."""


async def get_supervisor(db: AsyncSession) -> Agent | None:
    return (
        await db.execute(
            select(Agent).where(Agent.name == SUPERVISOR_NAME, Agent.owner_user_id.is_(None))
        )
    ).scalar_one_or_none()


async def ensure_supervisor(db: AsyncSession) -> Agent:
    """Crée l'agent système Superviseur au premier besoin (idempotent)."""
    sup = await get_supervisor(db)
    if sup is not None:
        return sup
    settings = get_settings()
    sup = Agent(owner_user_id=None, name=SUPERVISOR_NAME,
                description="Agent système qui planifie les missions en tâches.",
                mission_prompt=SUPERVISOR_PROMPT, category="système",
                model=settings.default_model, effort="high", max_parallel_tasks=4)
    db.add(sup)
    await db.commit()
    return sup


async def _default_provider(db: AsyncSession) -> Provider | None:
    return (await db.execute(select(Provider).where(Provider.is_default.is_(True)))).scalar_one_or_none()


async def _generate(db: AsyncSession, supervisor: Agent, prompt: str) -> str:
    provider_row = None
    if supervisor.provider_id:
        provider_row = await db.get(Provider, supervisor.provider_id)
    provider_row = provider_row or await _default_provider(db)
    if provider_row is None:
        raise RuntimeError("Aucun provider LLM configuré.")
    provider = llm.build_provider(provider_row)
    model = supervisor.model or provider_row.default_model or get_settings().default_model
    if provider_row.ptype == "openai" and (not model or model == get_settings().default_model):
        model = provider_row.default_model or model
    resp = await provider.create(model=model, system=supervisor.mission_prompt,
                                 messages=[{"role": "user", "content": prompt}],
                                 tools=[], max_tokens=get_settings().max_tokens, effort="high")
    return "".join(block_get(b, "text", "") for b in resp.blocks if block_type(b) == "text")


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Le superviseur n'a pas renvoyé de JSON exploitable.")
    return json.loads(text[start:end + 1])


async def make_plan(db: AsyncSession, mission_text: str, user: User) -> dict:
    """Génère un plan. Roster = agents visibles de l'utilisateur (les siens + système)."""
    supervisor = await ensure_supervisor(db)
    agents = (
        await db.execute(
            select(Agent).where((Agent.owner_user_id == user.id) | (Agent.owner_user_id.is_(None)))
        )
    ).scalars().all()
    roster = [{"name": a.name, "description": a.description} for a in agents]
    prompt = (f"# Mission de l'utilisateur\n{mission_text}\n\n"
              f"# Agents disponibles\n{json.dumps(roster, ensure_ascii=False, indent=2)}\n\n"
              "Produis le plan JSON.")
    plan = _parse_json(await _generate(db, supervisor, prompt))
    plan.setdefault("title", mission_text[:60])
    plan.setdefault("summary", "")
    plan.setdefault("new_agents", [])
    plan.setdefault("tasks", [])
    for i, t in enumerate(plan["tasks"]):
        t.setdefault("ref", f"t{i + 1}")
        t.setdefault("title", "")
        t.setdefault("depends_on", [])
    return plan


async def materialize(db: AsyncSession, mission: Mission, user: User) -> dict:
    """Crée les agents manquants (dédiés à l'utilisateur) et les tâches du plan validé."""
    from .agent_tools import task_workdir
    settings = get_settings()
    plan = mission.plan or {}
    existing = (
        await db.execute(
            select(Agent).where((Agent.owner_user_id == user.id) | (Agent.owner_user_id.is_(None)))
        )
    ).scalars().all()
    name_to_id = {a.name: a.id for a in existing}
    created_agents, errors = [], []

    default_provider = await _default_provider(db)
    default_model = default_provider.default_model if default_provider else settings.default_model

    for na in plan.get("new_agents", []):
        name = (na.get("name") or "").strip()
        if not name or name in name_to_id:
            continue
        agent = Agent(owner_user_id=user.id, name=name, description=na.get("description", ""),
                      mission_prompt=na.get("mission_prompt") or na.get("description", "") or name,
                      model=default_model or settings.default_model, effort=settings.default_effort)
        db.add(agent)
        await db.flush()
        name_to_id[name] = agent.id
        created_agents.append(name)

    ref_to_id, pending_deps = {}, []
    for t in plan.get("tasks", []):
        agent_id = name_to_id.get(t.get("agent"))
        if not agent_id:
            errors.append(f"Agent introuvable pour '{t.get('title') or t.get('ref')}' : {t.get('agent')}")
            continue
        task = Task(mission_id=mission.id, agent_id=agent_id, owner_user_id=user.id,
                    title=t.get("title", ""),
                    description=t.get("description") or t.get("title") or "(tâche sans description)",
                    created_by="supervisor", status="pending")
        db.add(task)
        await db.flush()
        task_workdir(task.id)
        ref_to_id[t["ref"]] = task.id
        pending_deps.append((task.id, t.get("depends_on") or []))

    for tid, deps in pending_deps:
        for d in deps:
            if d in ref_to_id:
                db.add(TaskLink(task_id=tid, linked_task_id=ref_to_id[d], kind="depends_on"))

    mission.status = "running"
    await db.commit()
    return {"created_agents": created_agents, "tasks": len(ref_to_id), "errors": errors}
