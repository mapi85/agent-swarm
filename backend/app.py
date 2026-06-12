"""Service back de l'essaim d'agents : API REST + supervision + planificateur."""
import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, planner, runtime, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
TEXT_PREVIEW_EXT = {".txt", ".md", ".markdown", ".json", ".csv", ".log", ".py", ".js", ".ts",
                    ".html", ".css", ".yml", ".yaml", ".xml", ".sh", ".ps1", ".ini", ".env", ".toml"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.get_conn()
    scheduler.recover_stale_state()
    task = asyncio.create_task(scheduler.scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="Essaim d'agents", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------------

class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    mission_prompt: str = Field(min_length=1)
    model: str = config.DEFAULT_MODEL
    effort: str = config.DEFAULT_EFFORT
    max_iterations: int = config.DEFAULT_MAX_ITERATIONS
    session_token_budget: int = config.DEFAULT_SESSION_TOKEN_BUDGET
    provider_id: int | None = None


class AgentUpdate(BaseModel):
    description: str | None = None
    mission_prompt: str | None = None
    model: str | None = None
    effort: str | None = None
    max_iterations: int | None = None
    session_token_budget: int | None = None
    provider_id: int | None = None
    clear_provider: bool = False    # true = revenir au provider par défaut


class TaskCreate(BaseModel):
    description: str = Field(min_length=1)


class SessionCreate(BaseModel):
    objective: str = Field(min_length=1)
    scheduled_at: str | None = None


class AnswerBody(BaseModel):
    response: str = Field(min_length=1)


class RunNowBody(BaseModel):
    comment: str = ""


class LinkResource(BaseModel):
    scope: str = "shared"
    name: str = Field(min_length=1)
    content: str = Field(min_length=1)        # URL ou texte de note
    kind: str = "link"                        # link | note
    description: str = ""
    agent_id: int | None = None
    task_id: int | None = None


class ProjectCreate(BaseModel):
    mission: str = Field(min_length=1)


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    ptype: str = "anthropic"                  # anthropic | openai
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    native_features: bool = True
    is_default: bool = False


class ProviderUpdate(BaseModel):
    name: str | None = None
    ptype: str | None = None
    base_url: str | None = None
    api_key: str | None = None                # None = inchangée
    default_model: str | None = None
    native_features: bool | None = None


# ---------------------------------------------------------------------------
# Vue d'ensemble
# ---------------------------------------------------------------------------

@app.get("/api/overview")
def overview():
    return {
        "agents": db.query_one("SELECT COUNT(*) AS c FROM agents")["c"],
        "agents_running": db.query_one("SELECT COUNT(*) AS c FROM agents WHERE status='running'")["c"],
        "sessions_running": db.running_sessions_count(),
        "sessions_planned": db.query_one("SELECT COUNT(*) AS c FROM sessions WHERE status='planned'")["c"],
        "tasks_pending": db.query_one("SELECT COUNT(*) AS c FROM tasks WHERE status='pending'")["c"],
        "projects_active": db.query_one("SELECT COUNT(*) AS c FROM projects WHERE status IN ('proposed','running')")["c"],
        "notifications_open": db.open_notifications_count(),
        "tokens_in": db.query_one("SELECT COALESCE(SUM(input_tokens),0) AS c FROM sessions")["c"],
        "tokens_out": db.query_one("SELECT COALESCE(SUM(output_tokens),0) AS c FROM sessions")["c"],
    }


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@app.get("/api/agents")
def agents_list():
    agents = db.list_agents()
    providers_by_id = {p["id"]: p for p in db.list_providers()}
    tokens = {t["id"]: t for t in db.tokens_by_agent()}
    default = db.default_provider()
    for a in agents:
        a["current_session"] = db.query_one(
            "SELECT id, number, objective, started_at FROM sessions WHERE agent_id=? AND status='running' LIMIT 1",
            (a["id"],))
        a["next_session"] = db.query_one(
            "SELECT id, number, objective, scheduled_at FROM sessions WHERE agent_id=? AND status='planned' "
            "ORDER BY scheduled_at LIMIT 1", (a["id"],))
        a["pending_tasks"] = len(db.pending_tasks(a["id"]))
        a["awaiting"] = len(db.open_questions(a["id"]))
        prov = providers_by_id.get(a["provider_id"]) or default
        a["provider_name"] = prov["name"] if prov else None
        tk = tokens.get(a["id"], {})
        a["tokens_in"] = tk.get("input_tokens", 0)
        a["tokens_out"] = tk.get("output_tokens", 0)
        # État synthétique pour la supervision.
        a["state"] = ("paused" if a["status"] == "paused"
                      else "running" if a["current_session"]
                      else "awaiting" if a["awaiting"]
                      else "scheduled" if a["next_session"]
                      else "idle")
    return agents


@app.post("/api/agents", status_code=201)
def agents_create(body: AgentCreate):
    if db.get_agent_by_name(body.name):
        raise HTTPException(409, f"Un agent nommé '{body.name}' existe déjà.")
    if body.provider_id is not None and not db.get_provider(body.provider_id):
        raise HTTPException(400, "Provider inconnu.")
    agent_id = db.create_agent(body.name, body.description, body.mission_prompt,
                               body.model, body.effort, body.max_iterations,
                               body.session_token_budget, body.provider_id)
    runtime.agent_workdir(db.get_agent(agent_id))
    return db.get_agent(agent_id)


@app.get("/api/agents/{agent_id}")
def agents_get(agent_id: int):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent inconnu.")
    return agent


@app.patch("/api/agents/{agent_id}")
def agents_update(agent_id: int, body: AgentUpdate):
    if not db.get_agent(agent_id):
        raise HTTPException(404, "Agent inconnu.")
    if body.provider_id is not None and not db.get_provider(body.provider_id):
        raise HTTPException(400, "Provider inconnu.")
    fields = {k: v for k, v in body.model_dump().items()
              if v is not None and k != "clear_provider"}
    if body.clear_provider:
        fields["provider_id"] = None
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE agents SET {cols} WHERE id = ?", (*fields.values(), agent_id))
    return db.get_agent(agent_id)


def _delete_resources_rows(rows: list[dict]) -> None:
    """Supprime des ressources et leurs fichiers sur disque."""
    for r in rows:
        if r["kind"] == "file" and r["filename"]:
            (config.RESOURCES_DIR / r["filename"]).unlink(missing_ok=True)
        db.delete_resource(r["id"])


@app.delete("/api/agents/{agent_id}")
def agents_delete(agent_id: int):
    """Suppression définitive d'un agent : historique purgé, tâches de mission non terminées
    annulées (avec cascade aval), ressources liées supprimées. Le workdir reste sur le disque."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent inconnu.")
    if agent["status"] == "running" or db.query_one(
            "SELECT id FROM sessions WHERE agent_id = ? AND status = 'running' LIMIT 1", (agent_id,)):
        raise HTTPException(409, "Session en cours — interromps-la d'abord.")
    open_tasks = db.query("SELECT * FROM tasks WHERE agent_id = ? AND status IN ('pending','in_progress')",
                          (agent_id,))
    pids = set()
    for t in open_tasks:
        db.update_task(t["id"], status="cancelled", result="Annulée : agent supprimé.",
                       completed_at=db.now())
        if t["project_id"]:
            db.cancel_downstream(t["id"], t["project_id"])
            pids.add(t["project_id"])
    for pid in pids:
        db.refresh_project_status(pid)
    _delete_resources_rows(db.query("SELECT * FROM resources WHERE scope = 'agent' AND agent_id = ?",
                                    (agent_id,)))
    workdir = runtime.agent_workdir(agent)
    db.delete_agent(agent_id)
    return {"deleted": True, "cancelled_tasks": len(open_tasks), "workdir_kept": str(workdir)}


@app.post("/api/agents/{agent_id}/pause")
def agents_pause(agent_id: int):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent inconnu.")
    if agent["status"] == "running":
        raise HTTPException(409, "Session en cours — interromps-la d'abord.")
    db.set_agent_status(agent_id, "paused")
    return db.get_agent(agent_id)


@app.post("/api/agents/{agent_id}/resume")
def agents_resume(agent_id: int):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent inconnu.")
    if agent["status"] == "paused":
        db.set_agent_status(agent_id, "idle")
    return db.get_agent(agent_id)


# ---------------------------------------------------------------------------
# Tâches / Sessions
# ---------------------------------------------------------------------------

@app.get("/api/services")
def services_list(status: str | None = None):
    return db.list_services(status)


@app.get("/api/agents/{agent_id}/services")
def agent_services(agent_id: int):
    return db.services_for_agent(agent_id)


@app.get("/api/agents/{agent_id}/tasks")
def tasks_list(agent_id: int):
    return db.tasks_for_agent(agent_id)


@app.post("/api/agents/{agent_id}/tasks", status_code=201)
def tasks_create(agent_id: int, body: TaskCreate):
    if not db.get_agent(agent_id):
        raise HTTPException(404, "Agent inconnu.")
    return db.get_task(db.create_task(agent_id, "user", body.description))


@app.get("/api/agents/{agent_id}/sessions")
def sessions_list(agent_id: int):
    return db.sessions_for_agent(agent_id)


@app.post("/api/agents/{agent_id}/sessions", status_code=201)
def sessions_create(agent_id: int, body: SessionCreate):
    if not db.get_agent(agent_id):
        raise HTTPException(404, "Agent inconnu.")
    return db.get_session(db.create_session(agent_id, body.objective, body.scheduled_at))


@app.get("/api/sessions/{session_id}")
def sessions_get(session_id: int):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session inconnue.")
    return session


@app.post("/api/sessions/{session_id}/run-now")
def sessions_run_now(session_id: int, body: RunNowBody):
    """Avance une session en attente à maintenant (lancée au prochain tick du planificateur),
    avec un commentaire optionnel injecté dans le contexte initial de la session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session inconnue.")
    if session["status"] != "planned":
        raise HTTPException(409, "Seule une session en attente (planifiée) peut être lancée manuellement.")
    fields = {"scheduled_at": db.now()}
    note = body.comment.strip()
    if note:
        # Cumule avec une éventuelle note précédente.
        fields["user_note"] = (session.get("user_note") + "\n---\n" + note) if session.get("user_note") else note
    db.update_session(session_id, **fields)
    agent = db.get_agent(session["agent_id"])
    warnings = []
    if agent["status"] == "paused":
        warnings.append("L'agent est en pause — clique « Reprendre » pour que la session démarre.")
    elif agent["status"] == "running":
        warnings.append("Une session de cet agent est déjà en cours — celle-ci démarrera dès qu'il sera libre.")
    if db.open_questions(agent["id"]):
        warnings.append("L'agent attend une réponse à une question (cloche 🔔) — il ne redémarrera "
                        "qu'une fois la réponse donnée.")
    return {"advanced": True, "session": db.get_session(session_id), "warnings": warnings}


@app.post("/api/sessions/{session_id}/interrupt")
def sessions_interrupt(session_id: int):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session inconnue.")
    if session["status"] == "planned":
        db.update_session(session_id, status="interrupted", ended_at=db.now())
        return {"interrupted": True, "was": "planned"}
    if runtime.interrupt_session(session_id):
        return {"interrupted": True, "was": "running"}
    raise HTTPException(409, "La session n'est pas en cours d'exécution.")


@app.get("/api/sessions/{session_id}/events")
def session_events(session_id: int, after: int = 0):
    if not db.get_session(session_id):
        raise HTTPException(404, "Session inconnue.")
    return db.events_for_session(session_id, after_id=after)


# ---------------------------------------------------------------------------
# Notifications / sollicitations
# ---------------------------------------------------------------------------

@app.get("/api/notifications")
def notifications_list(status: str | None = None):
    return db.list_notifications(status)


@app.post("/api/notifications/{nid}/answer")
def notifications_answer(nid: int, body: AnswerBody):
    n = db.get_notification(nid)
    if not n:
        raise HTTPException(404, "Notification inconnue.")
    if n["type"] != "question":
        raise HTTPException(400, "Cette notification n'est pas une question.")
    db.answer_notification(nid, body.response)
    # Si l'agent n'attend plus aucune réponse, avancer sa session planifiée pour une reprise prompte.
    if not db.open_questions(n["agent_id"]):
        db.advance_planned_sessions(n["agent_id"])
    return db.get_notification(nid)


@app.post("/api/notifications/{nid}/dismiss")
def notifications_dismiss(nid: int):
    if not db.get_notification(nid):
        raise HTTPException(404, "Notification inconnue.")
    db.dismiss_notification(nid)
    return {"dismissed": True}


# ---------------------------------------------------------------------------
# Ressources
# ---------------------------------------------------------------------------

@app.get("/api/resources")
def resources_list(scope: str | None = None, agent_id: int | None = None,
                   task_id: int | None = None, project_id: int | None = None):
    return db.list_resources(scope, agent_id, task_id, project_id)


@app.post("/api/resources/link", status_code=201)
def resources_link(body: LinkResource):
    rid = db.create_resource(body.scope, body.agent_id, body.task_id, body.name, body.kind,
                             None, body.content, body.description, len(body.content), "user")
    return db.get_resource(rid)


@app.post("/api/resources/upload", status_code=201)
async def resources_upload(file: UploadFile = File(...), scope: str = Form("shared"),
                           agent_id: int | None = Form(None), task_id: int | None = Form(None),
                           description: str = Form("")):
    data = await file.read()
    safe = Path(file.filename or "fichier").name
    rid = db.create_resource(scope, agent_id, task_id, safe, "file", "", None, description, len(data), "user")
    stored = f"{rid}_{safe}"
    (config.RESOURCES_DIR / stored).write_bytes(data)
    db.execute("UPDATE resources SET filename = ? WHERE id = ?", (stored, rid))
    return db.get_resource(rid)


@app.get("/api/resources/{rid}/content")
def resources_content(rid: int, download: bool = False):
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404, "Ressource inconnue.")
    if r["kind"] == "file" and r["filename"]:
        fp = config.RESOURCES_DIR / r["filename"]
        if not fp.exists():
            raise HTTPException(404, "Fichier manquant.")
        return FileResponse(fp, filename=r["name"],
                            media_type="application/octet-stream" if download else None)
    headers = None
    if download:
        # Nom de fichier propre pour les notes/liens (sinon le navigateur invente un nom).
        name = re.sub(r'[\\/:*?"<>|]', "_", r["name"]).strip() or f"ressource-{rid}"
        if "." not in name:
            name += ".txt"
        headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    return PlainTextResponse(r.get("content") or "", headers=headers)


@app.delete("/api/resources/{rid}")
def resources_delete(rid: int):
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404, "Ressource inconnue.")
    if r["kind"] == "file" and r["filename"]:
        (config.RESOURCES_DIR / r["filename"]).unlink(missing_ok=True)
    db.delete_resource(rid)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Artefacts (fichiers produits dans le workdir d'un agent)
# ---------------------------------------------------------------------------

def _safe_workdir_path(agent: dict, rel: str) -> Path:
    wd = runtime.agent_workdir(agent).resolve()
    target = (wd / rel).resolve()
    if wd not in target.parents and target != wd:
        raise HTTPException(400, "Chemin hors du workdir.")
    return target


@app.get("/api/agents/{agent_id}/artifacts")
def artifacts_list(agent_id: int):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent inconnu.")
    wd = runtime.agent_workdir(agent).resolve()
    out = []
    for p in wd.rglob("*"):
        if p.is_file():
            st = p.stat()
            rel = p.relative_to(wd).as_posix()
            out.append({"path": rel, "size": st.st_size, "mtime": st.st_mtime,
                        "dir": rel.split("/")[0] if "/" in rel else "."})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


@app.get("/api/agents/{agent_id}/artifact")
def artifact_content(agent_id: int, path: str, download: bool = False):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent inconnu.")
    fp = _safe_workdir_path(agent, path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "Artefact introuvable.")
    if download or fp.suffix.lower() not in TEXT_PREVIEW_EXT:
        return FileResponse(fp, filename=fp.name,
                            media_type="application/octet-stream" if download else None)
    return PlainTextResponse(fp.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# Mémoire structurée (supervision)
# ---------------------------------------------------------------------------

@app.get("/api/agents/{agent_id}/memories")
def memories_list(agent_id: int, scope: str | None = None):
    if not db.get_agent(agent_id):
        raise HTTPException(404, "Agent inconnu.")
    return db.memory_list(agent_id, scope)


@app.delete("/api/agents/{agent_id}/memories/{mid}")
def memories_delete(agent_id: int, mid: int):
    db.execute("DELETE FROM memories WHERE id = ? AND agent_id = ?", (mid, agent_id))
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Providers LLM
# ---------------------------------------------------------------------------

def _provider_payload(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "api_key"}
    out["api_key_set"] = bool(p["api_key"])
    out["agents_count"] = db.provider_in_use(p["id"])
    return out


@app.get("/api/providers")
def providers_list():
    return [_provider_payload(p) for p in db.list_providers()]


@app.post("/api/providers", status_code=201)
def providers_create(body: ProviderCreate):
    if body.ptype not in ("anthropic", "openai"):
        raise HTTPException(400, "Type de provider invalide (anthropic | openai).")
    if db.get_provider_by_name(body.name.strip()):
        raise HTTPException(409, f"Un provider nommé '{body.name}' existe déjà.")
    pid = db.create_provider(body.name.strip(), body.ptype, body.base_url.strip(),
                             body.api_key.strip(), body.default_model.strip(),
                             body.native_features, body.is_default)
    return _provider_payload(db.get_provider(pid))


@app.patch("/api/providers/{pid}")
def providers_update(pid: int, body: ProviderUpdate):
    if not db.get_provider(pid):
        raise HTTPException(404, "Provider inconnu.")
    if body.ptype is not None and body.ptype not in ("anthropic", "openai"):
        raise HTTPException(400, "Type de provider invalide (anthropic | openai).")
    if body.name is not None:
        other = db.get_provider_by_name(body.name.strip())
        if other and other["id"] != pid:
            raise HTTPException(409, f"Un provider nommé '{body.name}' existe déjà.")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    if "native_features" in fields:
        fields["native_features"] = 1 if fields["native_features"] else 0
    if fields:
        db.update_provider(pid, **fields)
    return _provider_payload(db.get_provider(pid))


@app.post("/api/providers/{pid}/default")
def providers_set_default(pid: int):
    if not db.get_provider(pid):
        raise HTTPException(404, "Provider inconnu.")
    db.set_default_provider(pid)
    return _provider_payload(db.get_provider(pid))


@app.delete("/api/providers/{pid}")
def providers_delete(pid: int):
    p = db.get_provider(pid)
    if not p:
        raise HTTPException(404, "Provider inconnu.")
    if len(db.list_providers()) == 1:
        raise HTTPException(409, "Impossible de supprimer le dernier provider.")
    n = db.provider_in_use(pid)
    db.delete_provider(pid)
    return {"deleted": True, "agents_reset_to_default": n}


# ---------------------------------------------------------------------------
# Statistiques de consommation
# ---------------------------------------------------------------------------

@app.get("/api/stats/tokens")
def stats_tokens():
    return {
        "total": {
            "input_tokens": db.query_one("SELECT COALESCE(SUM(input_tokens),0) AS c FROM sessions")["c"],
            "output_tokens": db.query_one("SELECT COALESCE(SUM(output_tokens),0) AS c FROM sessions")["c"],
        },
        "by_agent": db.tokens_by_agent(),
        "by_provider": db.tokens_by_provider(),
        "by_project": db.tokens_by_project(),
    }


# ---------------------------------------------------------------------------
# Projets / missions (agent superviseur)
# ---------------------------------------------------------------------------

def _project_payload(proj: dict) -> dict:
    import json as _json
    proj = dict(proj)
    proj["plan"] = _json.loads(proj["plan"]) if proj.get("plan") else None
    tasks = db.project_tasks(proj["id"])
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["blocked"] = db.task_is_blocked(t)
        deps = _json.loads(t["depends_on"] or "[]")
        t["deps"] = [{"id": d, "title": by_id[d]["title"] if d in by_id else f"#{d}",
                      "agent": by_id[d]["agent_name"] if d in by_id else "?"} for d in deps]
    proj["tasks"] = tasks
    proj["progress"] = {
        "total": len(tasks),
        "done": sum(1 for t in tasks if t["status"] == "done"),
        "failed": sum(1 for t in tasks if t["status"] == "failed"),
        "running": sum(1 for t in tasks if t["status"] == "in_progress"),
    }
    proj["tokens_in"] = sum(t.get("input_tokens") or 0 for t in tasks)
    proj["tokens_out"] = sum(t.get("output_tokens") or 0 for t in tasks)
    return proj


@app.get("/api/projects")
def projects_list(include_archived: bool = False):
    return [_project_payload(p) for p in db.list_projects(include_archived)]


@app.get("/api/projects/{pid}")
def projects_get(pid: int):
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(404, "Projet inconnu.")
    return _project_payload(proj)


@app.post("/api/projects", status_code=201)
async def projects_create(body: ProjectCreate):
    """Le superviseur comprend la mission et propose un plan (statut 'proposed')."""
    try:
        plan = await planner.make_plan(body.mission)
    except Exception as exc:
        raise HTTPException(502, f"Échec de la planification : {exc}")
    pid = db.create_project(plan.get("title") or body.mission[:60], body.mission,
                            plan.get("summary", ""), plan)
    return _project_payload(db.get_project(pid))


@app.post("/api/projects/{pid}/replan")
async def projects_replan(pid: int):
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(404, "Projet inconnu.")
    if proj["status"] != "proposed":
        raise HTTPException(409, "Seul un plan non encore validé peut être régénéré.")
    try:
        plan = await planner.make_plan(proj["mission"])
    except Exception as exc:
        raise HTTPException(502, f"Échec de la planification : {exc}")
    db.update_project(pid, summary=plan.get("summary", ""), plan=__import__("json").dumps(plan, ensure_ascii=False),
                      title=plan.get("title") or proj["title"])
    return _project_payload(db.get_project(pid))


@app.post("/api/projects/{pid}/approve")
def projects_approve(pid: int):
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(404, "Projet inconnu.")
    if proj["status"] != "proposed":
        raise HTTPException(409, "Ce projet a déjà été validé.")
    result = planner.materialize(proj)
    return {"project": _project_payload(db.get_project(pid)), **result}


@app.post("/api/projects/{pid}/archive")
def projects_archive(pid: int):
    if not db.get_project(pid):
        raise HTTPException(404, "Projet inconnu.")
    db.update_project(pid, status="archived")
    return {"archived": True}


@app.delete("/api/projects/{pid}")
def projects_delete(pid: int):
    """Suppression définitive d'une mission : ses tâches (et leurs ressources liées) sont
    supprimées. Refusée si une tâche est en cours d'exécution."""
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(404, "Projet inconnu.")
    tasks = db.project_tasks(pid)
    if any(t["status"] == "in_progress" for t in tasks):
        raise HTTPException(409, "Une tâche de la mission est en cours d'exécution — "
                                 "interromps la session de l'agent d'abord.")
    ids = [t["id"] for t in tasks]
    if ids:
        ph = ",".join("?" * len(ids))
        _delete_resources_rows(db.query(
            f"SELECT * FROM resources WHERE scope = 'task' AND task_id IN ({ph})", tuple(ids)))
        db.execute(f"DELETE FROM tasks WHERE id IN ({ph})", tuple(ids))
    db.delete_project(pid)
    return {"deleted": True, "tasks_deleted": len(ids)}


# Front statique — monté en dernier
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
