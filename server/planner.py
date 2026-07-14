"""Superviseur de missions : à partir d'une mission décrite par un utilisateur,
produit une FEUILLE DE ROUTE d'étapes qu'il exécutera LUI-MÊME, puis la
matérialise en UNE tâche confiée au superviseur (exécution solo).

Le superviseur est un agent SYSTÈME (owner NULL, nom réservé). Il réalise la
mission de bout en bout (analyse, audit, étapes, livrables) SANS déléguer à
d'autres agents : pas de goulot d'étranglement, pas d'attente, pas de dépendance.
Il peut créer/modérer des agents pour la plateforme, mais la mission est sienne.
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import llm
from .config import get_settings
from .llm import block_get, block_type
from .models import Agent, Mission, Provider, Task, User

log = logging.getLogger("swarm.planner")

SUPERVISOR_NAME = "Superviseur"

# Prompt d'EXÉCUTION stocké sur l'agent (utilisé à chaque session runtime).
SUPERVISOR_EXEC_PROMPT = """Tu es l'agent SUPERVISEUR de l'essaim. Tu réalises toi-même, de bout en bout, les missions qu'on te confie.

Principe directeur — EXÉCUTION SOLO, SANS DÉLÉGATION :
- Tu accomplis la mission toi-même : analyse, audit, recherches, calculs, étapes, livrables.
- Tu NE délègues PAS le travail de la mission à d'autres agents (pas de create_task/handoff pour la mission).
- Tu ne dépends d'aucun autre agent. Tu ne crées ni goulot d'étranglement ni attente supplémentaire.
- Tu peux créer ou modifier des agents pour la plateforme, mais la mission reste TIENNE à exécuter.

Méthode :
- Travaille en sessions bornées : progresse concrètement à chaque session (un résultat partiel réel), puis appelle finish_session avec un rapport clair et un next_objective pour continuer.
- Continue ainsi jusqu'à accomplissement complet, alors appelle finish_session avec task_completed=true et le livrable final dans task_result.
- Utilise tes outils (shell, fichiers, web_search/web_fetch, mémoire) pour produire du travail réel, pas des plans indéfinis.
- Sobriété : calcul déterministe dans des scripts quand c'est possible, mémoire structurée, avance par étapes vérifiables."""

# Prompt de PLANIFICATION (one-shot, inline, non stocké sur l'agent).
SUPERVISOR_PLAN_PROMPT = """Tu es l'agent SUPERVISEUR. À partir d'une mission décrite par un utilisateur, tu produis
une FEUILLE DE ROUTE d'étapes que TU exécuteras toi-même pour accomplir la mission.

Principe : TU réalises la mission seul, sans déléguer à d'autres agents. La feuille de route décrit
TES propres étapes (analyse, audit, recherches, implémentation, vérification…) — pas des tâches confiées à d'autres.

Règles :
- Le minimum d'étapes utiles, claires et vérifiables. Chaque étape = un livrable ou résultat concret.
- Pas d'affectation à un agent, pas de depends_on : c'est TON cheminement, séquentiel.
- Sois concret et complet dans chaque description (contexte, attendu, critère de réussite).
- N'introduis aucune dépendance vis-à-vis d'un autre agent.

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour :
{
  "title": "titre court de la mission",
  "summary": "démarche en 2-4 phrases",
  "steps": [
    {"title": "titre de l'étape", "description": "ce que tu feras, l'attendu, le critère de réussite"}
  ]
}
steps peut être vide si la mission est directe."""


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
                description="Agent système qui réalise les missions lui-même, de bout en bout.",
                mission_prompt=SUPERVISOR_EXEC_PROMPT, category="système",
                model=settings.default_model, effort="high", max_parallel_tasks=4)
    db.add(sup)
    await db.commit()
    return sup


async def _default_provider(db: AsyncSession) -> Provider | None:
    return (await db.execute(select(Provider).where(Provider.is_default.is_(True)))).scalar_one_or_none()


async def _generate(db: AsyncSession, supervisor: Agent, prompt: str, system: str | None = None) -> str:
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
    resp = await provider.create(model=model, system=system or supervisor.mission_prompt,
                                 messages=[{"role": "user", "content": prompt}],
                                 tools=[], max_tokens=get_settings().max_tokens, effort="high")
    return "".join(block_get(b, "text", "") for b in resp.blocks if block_type(b) == "text")


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Le superviseur n'a pas renvoyé de JSON exploitable.")
    return json.loads(text[start:end + 1])


async def make_plan(db: AsyncSession, mission_text: str, user: User) -> dict:
    """Génère une feuille de route d'étapes (exécutées par le superviseur lui-même)."""
    supervisor = await ensure_supervisor(db)
    prompt = (f"# Mission de l'utilisateur\n{mission_text}\n\n"
              "Produis la feuille de route JSON (tes propres étapes d'exécution).")
    plan = _parse_json(await _generate(db, supervisor, prompt, system=SUPERVISOR_PLAN_PROMPT))
    plan.setdefault("title", mission_text[:60])
    plan.setdefault("summary", "")
    plan.setdefault("steps", [])
    for s in plan["steps"]:
        s.setdefault("title", "")
        s.setdefault("description", "")
    return plan


async def materialize(db: AsyncSession, mission: Mission, user: User) -> dict:
    """Matérialise la mission en UNE tâche confiée au superviseur (exécution solo).
    La feuille de route est intégrée à la description de la tâche."""
    from .agent_tools import task_workdir
    supervisor = await ensure_supervisor(db)
    plan = mission.plan or {}
    steps = plan.get("steps", [])

    roadmap = "\n".join(f"{i}. {s.get('title', '')} — {s.get('description', '')}".strip(" —")
                        for i, s in enumerate(steps, 1))
    description = (
        f"## Mission\n{mission.mission}\n\n"
        f"## Feuille de route (à exécuter par toi-même, sans délégation)\n"
        f"{roadmap or '(mission directe — procède selon ton jugement)'}\n\n"
        "Réalisée par le superviseur. Avance par sessions : un progrès concret par session, "
        "puis finish_session(report, next_objective) jusqu'à task_completed=true."
    )
    task = Task(mission_id=mission.id, agent_id=supervisor.id, owner_user_id=user.id,
                title=plan.get("title") or mission.title or mission.mission[:80],
                description=description, created_by="supervisor", status="pending")
    db.add(task)
    await db.flush()
    task_workdir(task.id)

    mission.status = "running"
    await db.commit()
    return {"created_agents": [], "tasks": 1, "task_id": task.id, "errors": []}
