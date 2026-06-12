"""Agent superviseur : comprend une mission et propose un plan décomposé en tâches
(parallèles / séquentielles) confiées aux agents de l'essaim."""
import json

from . import config, db, providers
from .providers import block_get, block_type

SYSTEM = """Tu es l'agent SUPERVISEUR d'un essaim d'agents autonomes. À partir d'une mission décrite par
l'utilisateur, tu produis un PLAN clair et facile à suivre : une décomposition en tâches distinctes confiées
aux agents, certaines en parallèle, d'autres séquentielles (une tâche peut dépendre du résultat d'une autre).

Règles :
- Reste simple et lisible : le minimum de tâches nécessaires, pas d'usine à gaz.
- Confie chaque tâche à un agent existant adapté (par son nom). Ne propose un nouvel agent que si AUCUN agent
  existant ne convient, en le décrivant précisément.
- Exprime les enchaînements via depends_on : une tâche liste les refs des tâches dont elle dépend. Pas de
  dépendance = peut démarrer en parallèle. Une tâche dépendante reçoit automatiquement le résultat des tâches
  dont elle dépend.
- Chaque description de tâche doit être autonome et complète (contexte, attendu, critères de réussite).

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, de la forme :
{
  "title": "titre court de la mission",
  "summary": "explication du plan en 2-4 phrases : étapes, ce qui est parallèle, ce qui s'enchaîne",
  "new_agents": [{"name": "...", "description": "...", "mission_prompt": "..."}],
  "tasks": [
    {"ref": "t1", "agent": "nom-agent", "title": "titre court", "description": "description complète", "depends_on": []},
    {"ref": "t2", "agent": "nom-agent", "title": "...", "description": "...", "depends_on": ["t1"]}
  ]
}
new_agents peut être une liste vide. Les refs sont locales au plan (t1, t2, …)."""


async def _generate(prompt: str) -> str:
    provider, row = providers.default_provider_instance()
    model = config.DEFAULT_MODEL if row["ptype"] == "anthropic" \
        else (row["default_model"] or config.DEFAULT_MODEL)
    resp = await provider.create(model=model, system=SYSTEM,
                                 messages=[{"role": "user", "content": prompt}],
                                 tools=[], max_tokens=config.MAX_TOKENS, effort="high")
    return "".join(block_get(b, "text", "") for b in resp.blocks if block_type(b) == "text")


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Le superviseur n'a pas renvoyé de JSON exploitable.")
    return json.loads(text[start:end + 1])


async def make_plan(mission: str) -> dict:
    roster = [{"name": a["name"], "description": a["description"]} for a in db.list_agents()]
    prompt = (f"# Mission de l'utilisateur\n{mission}\n\n"
              f"# Agents existants dans l'essaim\n{json.dumps(roster, ensure_ascii=False, indent=2)}\n\n"
              "Produis le plan JSON.")
    plan = _parse_json(await _generate(prompt))
    # Normalisation défensive
    plan.setdefault("title", mission[:60])
    plan.setdefault("summary", "")
    plan.setdefault("new_agents", [])
    plan.setdefault("tasks", [])
    for i, t in enumerate(plan["tasks"]):
        t.setdefault("ref", f"t{i + 1}")
        t.setdefault("title", "")
        t.setdefault("depends_on", [])
    return plan


def materialize(project: dict) -> dict:
    """Crée les agents manquants et les tâches du plan validé, avec leurs dépendances."""
    plan = json.loads(project["plan"])
    name_to_id = {a["name"]: a["id"] for a in db.list_agents()}
    created_agents, errors = [], []

    for na in plan.get("new_agents", []):
        name = (na.get("name") or "").strip()
        if not name or name in name_to_id:
            continue
        aid = db.create_agent(name, na.get("description", ""),
                              na.get("mission_prompt") or na.get("description", "") or name,
                              config.DEFAULT_MODEL, config.DEFAULT_EFFORT, config.DEFAULT_MAX_ITERATIONS)
        name_to_id[name] = aid
        created_agents.append(name)

    ref_to_id, pending_deps = {}, []
    for t in plan.get("tasks", []):
        agent_id = name_to_id.get(t.get("agent"))
        if not agent_id:
            errors.append(f"Agent introuvable pour la tâche '{t.get('title') or t.get('ref')}': {t.get('agent')}")
            continue
        desc = t.get("description") or t.get("title") or "(tâche sans description)"
        tid = db.create_task(agent_id, "supervisor", desc, project_id=project["id"],
                             depends_on=[], title=t.get("title", ""))
        ref_to_id[t["ref"]] = tid
        pending_deps.append((tid, t.get("depends_on") or []))

    for tid, deps in pending_deps:
        dep_ids = [ref_to_id[d] for d in deps if d in ref_to_id]
        if dep_ids:
            db.update_task(tid, depends_on=json.dumps(dep_ids))

    db.update_project(project["id"], status="running")
    return {"created_agents": created_agents, "tasks": len(ref_to_id), "errors": errors}
