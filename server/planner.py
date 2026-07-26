"""Superviseur de missions : ARCHITECTE D'AGENTS.

À partir d'une mission décrite par l'utilisateur (un objectif, une finalité),
le Superviseur analyse et propose un PLAN DE DÉPLOIEMENT d'agents : pour chaque
agent à créer, il définit son rôle, sa cadence, son effort, sa coordination.
Après validation, il CRÉE les agents (dédiés à l'utilisateur, en pause) et
fournit un guide de déclenchement. La mission est alors archivée : elle a servi
à accoucher du dispositif d'agents, ce sont ensuite les agents qui vivent.

Cas ONE-SHOT (rare, sur demande explicite de l'utilisateur) : si créer un agent
n'a pas de sens (tâche ponctuelle unique), le plan le signale et le Superviseur
réalise la mission lui-même en une tâche solo.

Le Superviseur est un agent SYSTÈME (owner NULL, nom réservé).
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

# Prompt de RÔLE stocké sur l'agent (sert au cas one-shot et à ses outils).
SUPERVISOR_EXEC_PROMPT = """Tu es l'agent SUPERVISEUR de l'essaim : ARCHITECTE D'AGENTS.

Ton rôle principal : à partir d'une mission (un objectif que l'utilisateur veut atteindre), concevoir
et déployer les AGENTS qui accompliront cette mission dans la durée. Tu ne fais pas le travail de la
mission toi-même — tu construis les agents qui le feront, tu les configures (rôle, cadence, effort,
coordination) et tu expliques à l'utilisateur comment les déclencher.

Exception ONE-SHOT (rare) : si la mission est une tâche ponctuelle unique pour laquelle créer un agent
permanent n'aurait pas de sens, tu peux la réaliser toi-même en une fois — mais seulement si c'est
clairement le cas ou si l'utilisateur le demande explicitement.

Tu peux créer/modifier des agents (outils de gestion d'agents), lire l'état de la plateforme, et
utiliser tes outils pour analyser un besoin avant de proposer un déploiement."""

# Prompt de PLANIFICATION (one-shot, inline, non stocké sur l'agent).
SUPERVISOR_PLAN_PROMPT = """Tu es l'agent SUPERVISEUR, ARCHITECTE D'AGENTS. À partir de la mission décrite
par l'utilisateur, tu conçois un PLAN DE DÉPLOIEMENT D'AGENTS : quels agents créer pour accomplir la
mission dans la durée, comment les configurer, et comment l'utilisateur les déclenche.

Principe : tu ne réalises PAS la mission toi-même. Tu construis le dispositif d'agents qui la portera.
Pour CHAQUE agent, tu définis précisément :
- name : nom court, explicite, en minuscules avec des tirets (ex. "veilleur-crypto").
- role : une phrase — ce que fait l'agent.
- category : un thème court pour regrouper (ex. "Veille", "Analyse", "Rédaction").
- mission_prompt : le prompt système COMPLET de l'agent (sa mission permanente, ses règles, sa méthode,
  ses garde-fous). Sois concret et autonome : cet agent devra fonctionner seul à partir de ce texte.
- effort : niveau de réflexion du modèle — "low" (tâches simples/déterministes), "medium" (analyse
  courante), "high" (raisonnement complexe). Choisis au plus juste pour la sobriété en tokens.
- heartbeat_minutes : cadence d'auto-exécution en minutes si l'agent est RÉCURRENT (ex. 240 pour une
  veille toutes les 4h, 1440 pour un rapport quotidien). Mets 0 si l'agent est ÉVÉNEMENTIEL (déclenché
  seulement par un message/une tâche d'un autre agent ou de l'utilisateur).
- max_iterations : nombre d'étapes max par session (défaut raisonnable 40-60).
- session_token_budget : budget de tokens par session (ex. 40000-70000 ; 0 = illimité, à éviter).
- trigger : comment cet agent démarre concrètement (ex. "cadence automatique toutes les 4h" ou
  "recevoir un message de l'agent X" ou "l'utilisateur lui crée une tâche").

Règles :
- Le MINIMUM d'agents nécessaires. Un seul agent bien conçu vaut mieux que cinq qui se chevauchent.
- Pas de doublon de rôle entre agents. Coordination claire (qui déclenche qui).
- Sobriété : effort et cadence au plus juste ; calcul déterministe dans des scripts quand possible.
- one_shot=true UNIQUEMENT si créer un agent n'a aucun sens (tâche ponctuelle unique) — sinon toujours
  proposer des agents.

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour :
{
  "title": "titre court de la mission",
  "summary": "en 2-4 phrases : la finalité et l'approche de déploiement",
  "one_shot": false,
  "agents": [
    {"name": "...", "role": "...", "category": "...", "mission_prompt": "...",
     "effort": "medium", "heartbeat_minutes": 240, "max_iterations": 50,
     "session_token_budget": 50000, "trigger": "..."}
  ],
  "coordination": "comment les agents s'articulent entre eux (1-3 phrases)",
  "activation_guide": "pas à pas pour l'utilisateur : dans quel ordre activer/déclencher les agents pour lancer la mission"
}
Si one_shot=true : "agents" est vide, et tu ajoutes "steps" (liste d'étapes {title, description}) que
tu exécuteras toi-même à la place."""


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
                description="Agent système architecte : conçoit et déploie les agents des missions.",
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


_EFFORTS = {"low", "medium", "high", "max"}


async def make_plan(db: AsyncSession, mission_text: str, user: User) -> dict:
    """Génère un plan de déploiement d'agents (ou un plan one-shot)."""
    supervisor = await ensure_supervisor(db)
    prompt = (f"# Mission de l'utilisateur\n{mission_text}\n\n"
              "Conçois le plan de déploiement d'agents JSON (ou one_shot si vraiment ponctuel).")
    plan = _parse_json(await _generate(db, supervisor, prompt, system=SUPERVISOR_PLAN_PROMPT))
    plan.setdefault("title", mission_text[:60])
    plan.setdefault("summary", "")
    plan.setdefault("one_shot", False)
    plan.setdefault("agents", [])
    plan.setdefault("coordination", "")
    plan.setdefault("activation_guide", "")
    plan.setdefault("steps", [])
    # Normalisation défensive de chaque agent proposé.
    for a in plan["agents"]:
        a.setdefault("name", "")
        a.setdefault("role", "")
        a.setdefault("category", "")
        a.setdefault("mission_prompt", a.get("role", ""))
        a["effort"] = a.get("effort") if a.get("effort") in _EFFORTS else "medium"
        try:
            a["heartbeat_minutes"] = max(int(a.get("heartbeat_minutes") or 0), 0)
        except (TypeError, ValueError):
            a["heartbeat_minutes"] = 0
        try:
            a["max_iterations"] = min(max(int(a.get("max_iterations") or 50), 5), 500)
        except (TypeError, ValueError):
            a["max_iterations"] = 50
        try:
            a["session_token_budget"] = max(int(a.get("session_token_budget") or 50000), 0)
        except (TypeError, ValueError):
            a["session_token_budget"] = 50000
        a.setdefault("trigger", "")
    # Cohérence : si pas d'agents et pas explicitement one_shot, on force one_shot
    # seulement s'il y a des steps ; sinon on laisse tel quel (le front avertira).
    if not plan["agents"] and plan.get("steps"):
        plan["one_shot"] = True
    return plan


async def materialize(db: AsyncSession, mission: Mission, user: User) -> dict:
    """Matérialise le plan validé.

    Cas normal : CRÉE les agents (dédiés à l'utilisateur, EN PAUSE) et archive la
    mission. Cas one_shot : crée une tâche solo confiée au superviseur (ancien
    comportement, réservé aux missions vraiment ponctuelles)."""
    from .agent_tools import task_workdir
    settings = get_settings()
    plan = mission.plan or {}

    # --- ONE-SHOT : le superviseur réalise lui-même (tâche solo) ---
    if plan.get("one_shot") and not plan.get("agents"):
        supervisor = await ensure_supervisor(db)
        steps = plan.get("steps", [])
        roadmap = "\n".join(f"{i}. {s.get('title', '')} — {s.get('description', '')}".strip(" —")
                            for i, s in enumerate(steps, 1))
        description = (
            f"## Mission (one-shot)\n{mission.mission}\n\n"
            f"## Feuille de route\n{roadmap or '(procède selon ton jugement)'}\n\n"
            "Réalisée directement par le superviseur (aucun agent permanent nécessaire)."
        )
        task = Task(mission_id=mission.id, agent_id=supervisor.id, owner_user_id=user.id,
                    title=plan.get("title") or mission.title or mission.mission[:80],
                    description=description, created_by="supervisor", status="pending")
        db.add(task)
        await db.flush()
        task_workdir(task.id)
        mission.status = "running"
        await db.commit()
        return {"mode": "one_shot", "created_agents": [], "tasks": 1, "task_id": task.id, "errors": []}

    # --- CAS NORMAL : création des agents (en pause) ---
    existing = {
        a.name for a in (
            await db.execute(
                select(Agent).where((Agent.owner_user_id == user.id) | (Agent.owner_user_id.is_(None)))
            )
        ).scalars()
    }
    default_provider = await _default_provider(db)
    default_model = default_provider.default_model if default_provider else settings.default_model

    created, errors = [], []
    for a in plan.get("agents", []):
        name = (a.get("name") or "").strip()
        if not name:
            errors.append("Un agent proposé n'a pas de nom — ignoré.")
            continue
        if name in existing:
            errors.append(f"Un agent nommé « {name} » existe déjà — non recréé.")
            continue
        agent = Agent(
            owner_user_id=user.id,
            name=name,
            description=a.get("role", "")[:2000],
            mission_prompt=a.get("mission_prompt") or a.get("role") or name,
            category=a.get("category", "")[:120],
            model="",  # suit le provider par défaut
            effort=a.get("effort", "medium"),
            max_iterations=a.get("max_iterations", 50),
            session_token_budget=a.get("session_token_budget", 50000),
            heartbeat_minutes=a.get("heartbeat_minutes", 0),
            max_parallel_tasks=1,
            paused=True,  # créé EN PAUSE : l'utilisateur active quand il est prêt
        )
        db.add(agent)
        await db.flush()
        (settings.agents_dir / str(agent.id)).mkdir(parents=True, exist_ok=True)
        existing.add(name)
        created.append({"id": agent.id, "name": name, "role": a.get("role", ""),
                        "category": a.get("category", ""), "effort": agent.effort,
                        "heartbeat_minutes": agent.heartbeat_minutes, "trigger": a.get("trigger", "")})

    # La mission a rempli son office : on l'archive.
    mission.status = "archived"
    await db.commit()
    return {
        "mode": "agents",
        "created_agents": created,
        "coordination": plan.get("coordination", ""),
        "activation_guide": plan.get("activation_guide", ""),
        "errors": errors,
    }
