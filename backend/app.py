"""Service back de l'essaim d'agents : API REST + supervision + planificateur."""
import asyncio
import base64
import hashlib
import hmac as _hmac
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, notify, planner, providers, runtime, scheduler

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
# Auth (mot de passe unique, optionnel)
# ---------------------------------------------------------------------------

_TOKEN_TTL = 86400 * 30   # 30 jours

def _token_secret() -> str:
    return hashlib.sha256(config.ADMIN_PASSWORD.encode()).hexdigest()

def _create_token(profile_id) -> str:
    payload = json.dumps({"pid": profile_id, "exp": int(time.time()) + _TOKEN_TTL}, separators=(",", ":"))
    sig = _hmac.new(_token_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + sig

def _verify_token(token: str):
    try:
        p64, sig = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(p64 + "==").decode()
        expected = _hmac.new(_token_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        data = json.loads(payload)
        return data if data.get("exp", 0) >= int(time.time()) else None
    except Exception:
        return None

_AUTH_PUBLIC = {"/api/auth/login", "/api/auth/verify"}

@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in _AUTH_PUBLIC:
        return await call_next(request)
    if path == "/api/profiles" and request.method == "GET":
        return await call_next(request)
    if path.startswith("/api/webhooks/"):
        return await call_next(request)
    if not config.ADMIN_PASSWORD:           # pas de mot de passe → accès libre
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not token:                           # fallback : token en query param (images, téléchargements)
        token = request.query_params.get("token", "")
    if not token or not _verify_token(token):
        return JSONResponse({"detail": "Non authentifié"}, status_code=401)
    return await call_next(request)


class LoginBody(BaseModel):
    password: str = ""
    profile_id: int | None = None
    new_profile_name: str | None = None

@app.post("/api/auth/login")
def auth_login(body: LoginBody):
    if config.ADMIN_PASSWORD and not _hmac.compare_digest(
            body.password.encode(), config.ADMIN_PASSWORD.encode()):
        raise HTTPException(403, "Mot de passe incorrect")
    pid = body.profile_id
    if body.new_profile_name:
        name = body.new_profile_name.strip()
        if not name:
            raise HTTPException(400, "Nom de profil vide")
        if db.query_one("SELECT id FROM profiles WHERE name = ?", (name,)):
            raise HTTPException(409, f"Un profil nommé '{name}' existe déjà.")
        pid = db.create_profile(name)
    token = _create_token(pid) if config.ADMIN_PASSWORD else "open"
    return {"token": token, "profile_id": pid}

@app.get("/api/auth/verify")
def auth_verify(request: Request):
    if not config.ADMIN_PASSWORD:
        return {"ok": True, "open": True, "profile_id": None}
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    data = _verify_token(token)
    if not data:
        raise HTTPException(401, "Token invalide ou expiré")
    return {"ok": True, "profile_id": data.get("pid")}


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
    category: str = ""
    profile_id: int | None = None


class AgentUpdate(BaseModel):
    description: str | None = None
    mission_prompt: str | None = None
    model: str | None = None
    effort: str | None = None
    max_iterations: int | None = None
    session_token_budget: int | None = None
    provider_id: int | None = None
    clear_provider: bool = False    # true = revenir au provider par défaut
    category: str | None = None
    profile_id: int | None = None
    clear_profile: bool = False     # true = agent système (visible tous profils)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


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
    profile_id: int | None = None


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    ptype: str = "anthropic"                  # anthropic | openai
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    models: list[str] = []
    native_features: bool = True
    is_default: bool = False
    limit_short_tokens: int = 0
    limit_short_hours: int = 0
    limit_long_tokens: int = 0
    limit_long_days: int = 0


class ProviderUpdate(BaseModel):
    name: str | None = None
    ptype: str | None = None
    base_url: str | None = None
    api_key: str | None = None                # None = inchangée
    default_model: str | None = None
    models: list[str] | None = None
    native_features: bool | None = None
    limit_short_tokens: int | None = None
    limit_short_hours: int | None = None
    limit_long_tokens: int | None = None
    limit_long_days: int | None = None


class FetchModels(BaseModel):
    ptype: str = "anthropic"
    base_url: str = ""
    api_key: str = ""
    provider_id: int | None = None        # si fourni, complète URL/clé manquantes avec celles stockées


# ---------------------------------------------------------------------------
# Vue d'ensemble
# ---------------------------------------------------------------------------

@app.get("/api/overview")
def overview(profile_id: int | None = None):
    profile_filter = "AND (a.profile_id = ? OR a.profile_id IS NULL)" if profile_id else ""
    p = (profile_id,) if profile_id else ()
    agents_count = db.query_one(
        f"SELECT COUNT(*) AS c FROM agents a WHERE 1=1 {profile_filter}", p)["c"]
    running_agents = db.query_one(
        f"SELECT COUNT(*) AS c FROM agents a WHERE a.status='running' {profile_filter}", p)["c"]
    sessions_running = db.query_one(
        f"SELECT COUNT(*) AS c FROM sessions s JOIN agents a ON a.id=s.agent_id "
        f"WHERE s.status='running' {profile_filter}", p)["c"]
    sessions_planned = db.query_one(
        f"SELECT COUNT(*) AS c FROM sessions s JOIN agents a ON a.id=s.agent_id "
        f"WHERE s.status='planned' {profile_filter}", p)["c"]
    tasks_pending = db.query_one(
        f"SELECT COUNT(*) AS c FROM tasks t JOIN agents a ON a.id=t.agent_id "
        f"WHERE t.status='pending' {profile_filter}", p)["c"]
    notifs_open = db.query_one(
        f"SELECT COUNT(*) AS c FROM notifications n JOIN agents a ON a.id=n.agent_id "
        f"WHERE n.status='open' {profile_filter}", p)["c"]
    return {
        "agents": agents_count,
        "agents_running": running_agents,
        "sessions_running": sessions_running,
        "sessions_planned": sessions_planned,
        "tasks_pending": tasks_pending,
        "projects_active": db.query_one("SELECT COUNT(*) AS c FROM projects WHERE status IN ('proposed','running')")["c"],
        "notifications_open": notifs_open,
        "tokens_in": db.query_one("SELECT COALESCE(SUM(input_tokens),0) AS c FROM sessions")["c"],
        "tokens_out": db.query_one("SELECT COALESCE(SUM(output_tokens),0) AS c FROM sessions")["c"],
    }


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@app.get("/api/agents")
def agents_list(profile_id: int | None = None):
    agents = db.list_agents(profile_id)
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
        # Dernière session échouée (pour regroupement dashboard)
        last_s = db.query_one(
            "SELECT status FROM sessions WHERE agent_id=? ORDER BY id DESC LIMIT 1", (a["id"],))
        a["last_session_failed"] = bool(last_s and last_s["status"] == "failed"
                                        and a["state"] == "idle")
    return agents


@app.post("/api/agents", status_code=201)
def agents_create(body: AgentCreate):
    if db.get_agent_by_name(body.name):
        raise HTTPException(409, f"Un agent nommé '{body.name}' existe déjà.")
    if body.provider_id is not None and not db.get_provider(body.provider_id):
        raise HTTPException(400, "Provider inconnu.")
    if body.profile_id is not None and not db.get_profile(body.profile_id):
        raise HTTPException(400, "Profil inconnu.")
    agent_id = db.create_agent(body.name, body.description, body.mission_prompt,
                               body.model, body.effort, body.max_iterations,
                               body.session_token_budget, body.provider_id,
                               body.category.strip(), body.profile_id)
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
              if v is not None and k not in ("clear_provider", "clear_profile")}
    if "category" in fields:
        fields["category"] = fields["category"].strip()
    if body.clear_provider:
        fields["provider_id"] = None
    if body.clear_profile:
        fields["profile_id"] = None
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


@app.post("/api/sessions/{session_id}/retry")
def sessions_retry(session_id: int, body: RunNowBody):
    """Crée une nouvelle session planifiée avec le même objectif qu'une session échouée/interrompue."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session inconnue.")
    if session["status"] not in ("failed", "interrupted"):
        raise HTTPException(409, "Seule une session échouée ou interrompue peut être relancée.")
    new_id = db.create_session(session["agent_id"], session["objective"], db.now())
    note = body.comment.strip()
    if note:
        db.update_session(new_id, user_note=note)
    agent = db.get_agent(session["agent_id"])
    warnings = []
    if agent["status"] == "paused":
        warnings.append("L'agent est en pause — clique « Reprendre » pour que la session démarre.")
    if db.open_questions(agent["id"]):
        warnings.append("L'agent attend une réponse à une question (cloche 🔔).")
    return {"created_session_id": new_id, "warnings": warnings}


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

@app.get("/api/profiles")
def profiles_list():
    return db.list_profiles()


@app.post("/api/profiles", status_code=201)
def profiles_create(body: ProfileCreate):
    if db.query_one("SELECT id FROM profiles WHERE name = ?", (body.name,)):
        raise HTTPException(409, f"Un profil nommé '{body.name}' existe déjà.")
    pid = db.create_profile(body.name)
    return db.get_profile(pid)


@app.delete("/api/profiles/{pid}")
def profiles_delete(pid: int):
    p = db.get_profile(pid)
    if not p:
        raise HTTPException(404, "Profil inconnu.")
    if db.query_one("SELECT COUNT(*) AS c FROM profiles")["c"] <= 1:
        raise HTTPException(409, "Impossible de supprimer le dernier profil.")
    db.delete_profile(pid)
    return {"deleted": True}


@app.get("/api/notifications")
def notifications_list(status: str | None = None, agent_id: int | None = None,
                       type: str | None = None):
    return db.list_notifications(status, agent_id, type)


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
    MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 Mo
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Fichier trop volumineux ({len(data)//1024//1024} Mo). Limite : 100 Mo.")
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

def _clean_models(models: list[str]) -> list[str]:
    """Normalise une liste de modèles : trim, non vides, dédupliqués, ordre préservé."""
    seen, out = set(), []
    for m in models or []:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _provider_payload(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "api_key"}
    out["api_key_set"] = bool(p["api_key"])
    out["agents_count"] = db.provider_in_use(p["id"])
    try:
        out["models"] = json.loads(p.get("models") or "[]")
    except (TypeError, ValueError):
        out["models"] = []
    # Taux de consommation sur les fenêtres glissantes configurées.
    usage = {}
    if p["limit_short_tokens"] and p["limit_short_hours"]:
        used = db.provider_usage(p["name"], p["limit_short_hours"])
        usage["short"] = {"used": used, "limit": p["limit_short_tokens"],
                          "hours": p["limit_short_hours"],
                          "pct": round(100 * used / p["limit_short_tokens"])}
    if p["limit_long_tokens"] and p["limit_long_days"]:
        used = db.provider_usage(p["name"], p["limit_long_days"] * 24)
        usage["long"] = {"used": used, "limit": p["limit_long_tokens"],
                         "days": p["limit_long_days"],
                         "pct": round(100 * used / p["limit_long_tokens"])}
    out["usage"] = usage
    return out


@app.get("/api/providers")
def providers_list():
    return [_provider_payload(p) for p in db.list_providers()]


@app.post("/api/providers/fetch-models")
async def providers_fetch_models(body: FetchModels):
    """Récupère dynamiquement la liste des modèles via l'API du provider.
    Utilise les identifiants fournis ; à défaut (clé/URL vides), ceux du provider stocké."""
    ptype = body.ptype
    base_url = body.base_url.strip()
    api_key = body.api_key.strip()
    if body.provider_id:
        p = db.get_provider(body.provider_id)
        if not p:
            raise HTTPException(404, "Provider inconnu.")
        ptype = ptype or p["ptype"]
        base_url = base_url or p["base_url"]
        api_key = api_key or p["api_key"]
    if ptype not in ("anthropic", "openai"):
        raise HTTPException(400, "Type de provider invalide (anthropic | openai).")
    try:
        models = await providers.list_models(ptype, base_url, api_key)
    except Exception as exc:
        raise HTTPException(502, f"Échec de récupération des modèles : {type(exc).__name__}: {exc}")
    return {"models": models}


@app.post("/api/providers", status_code=201)
def providers_create(body: ProviderCreate):
    if body.ptype not in ("anthropic", "openai"):
        raise HTTPException(400, "Type de provider invalide (anthropic | openai).")
    if db.get_provider_by_name(body.name.strip()):
        raise HTTPException(409, f"Un provider nommé '{body.name}' existe déjà.")
    models = _clean_models(body.models)
    default_model = body.default_model.strip() or (models[0] if models else "")
    if default_model and default_model not in models:
        models.insert(0, default_model)
    pid = db.create_provider(body.name.strip(), body.ptype, body.base_url.strip(),
                             body.api_key.strip(), default_model,
                             body.native_features, body.is_default,
                             max(body.limit_short_tokens, 0), max(body.limit_short_hours, 0),
                             max(body.limit_long_tokens, 0), max(body.limit_long_days, 0), models)
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
    # Modèles + cohérence du modèle par défaut.
    cur = db.get_provider(pid)
    models = _clean_models(body.models) if body.models is not None else json.loads(cur["models"] or "[]")
    default_model = (body.default_model.strip() if body.default_model is not None else cur["default_model"]) \
        or (models[0] if models else "")
    if default_model and default_model not in models:
        models.insert(0, default_model)
    if body.models is not None or "default_model" in fields:
        fields["models"] = json.dumps(models)
        fields["default_model"] = default_model
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
def stats_tokens(days: int = 30, period: str | None = None, profile_id: int | None = None):
    summary = db.tokens_summary(period, profile_id)
    return {
        "total": {"input_tokens": summary["input_tokens"], "output_tokens": summary["output_tokens"]},
        "summary": summary,
        "by_agent": db.tokens_by_agent(period, profile_id),
        "by_provider": db.tokens_by_provider(period, profile_id),
        "by_project": db.tokens_by_project(period, profile_id),
        "by_category": db.tokens_by_category(period, profile_id),
        "by_day": list(reversed(db.tokens_by_day(days, period, profile_id))),
        **({"by_hour": db.tokens_by_hour(profile_id)} if period == "24h" else {}),
    }


@app.get("/api/categories")
def agents_categories():
    return db.agent_categories()


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
def projects_list(include_archived: bool = False, profile_id: int | None = None):
    return [_project_payload(p) for p in db.list_projects(include_archived, profile_id)]


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
                            plan.get("summary", ""), plan, body.profile_id)
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


# ---------------------------------------------------------------------------
# Canaux de notification
# ---------------------------------------------------------------------------

class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: str                       # email | telegram
    config: dict = {}
    enabled: bool = True
    assign_notifs: bool = False     # activer alertes pour tous les agents existants
    assign_questions: bool = False  # activer questions pour tous les agents existants


class ChannelUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    enabled: bool | None = None


class AgentChannelEntry(BaseModel):
    channel_id: int
    use_notifs: bool
    use_questions: bool


class SmtpConfig(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    from_addr: str = ""


def _mask_channel(ch: dict) -> dict:
    if ch and ch.get("type") == "telegram" and ch.get("config", {}).get("bot_token"):
        ch = {**ch, "config": {**ch["config"], "bot_token": "***"}}
    return ch


@app.get("/api/settings/smtp")
def settings_smtp_get():
    stored = db.get_setting("smtp_config", {})
    return {
        "host": stored.get("host") or config.SMTP_HOST,
        "port": int(stored.get("port") or config.SMTP_PORT or 587),
        "user": stored.get("user") or config.SMTP_USER,
        "from_addr": stored.get("from_addr") or config.SMTP_FROM,
        "password_set": bool(stored.get("password") or config.SMTP_PASSWORD),
    }


@app.put("/api/settings/smtp", status_code=204)
def settings_smtp_put(body: SmtpConfig):
    if not body.password:
        existing = db.get_setting("smtp_config", {})
        body.password = existing.get("password", "")
    db.set_setting("smtp_config", body.model_dump())


@app.post("/api/settings/smtp/test")
async def settings_smtp_test():
    cfg = notify._smtp_cfg()
    to = cfg["user"] or cfg["from_addr"]
    if not to:
        return {"result": "Configurez d'abord l'adresse utilisateur SMTP."}
    ch = {"type": "email", "config": {"to": to}}
    return {"result": await notify.send_test(ch)}


@app.get("/api/channels")
def channels_list():
    return [_mask_channel(ch) for ch in db.list_channels()]


@app.post("/api/channels", status_code=201)
async def channels_create(body: ChannelCreate, request: Request):
    if body.type not in ("email", "telegram"):
        raise HTTPException(400, "Type invalide (email | telegram).")
    cid = db.create_channel(body.name, body.type, body.config, body.enabled)
    if body.assign_notifs or body.assign_questions:
        db.assign_channel_to_all_agents(cid, body.assign_notifs, body.assign_questions)
    if body.type == "telegram" and body.config.get("bot_token"):
        base_url = str(request.base_url).rstrip("/")
        await notify.register_telegram_webhook(body.config["bot_token"], cid, base_url)
    return _mask_channel(db.get_channel(cid))


@app.patch("/api/channels/{cid}")
async def channels_update(cid: int, body: ChannelUpdate, request: Request):
    ch = db.get_channel(cid)
    if not ch:
        raise HTTPException(404, "Canal inconnu.")
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    if body.config is not None:
        merged = {**ch["config"]}
        for k, v in body.config.items():
            if v != "***":
                merged[k] = v
        fields["config"] = merged
    if fields:
        db.update_channel(cid, **fields)
    ch = db.get_channel(cid)
    if ch["type"] == "telegram":
        new_token = (body.config or {}).get("bot_token", "")
        if new_token and new_token != "***":
            base_url = str(request.base_url).rstrip("/")
            await notify.register_telegram_webhook(new_token, cid, base_url)
    return _mask_channel(ch)


@app.delete("/api/channels/{cid}")
def channels_delete(cid: int):
    if not db.get_channel(cid):
        raise HTTPException(404, "Canal inconnu.")
    db.delete_channel(cid)
    return {"deleted": True}


@app.post("/api/channels/{cid}/test")
async def channels_test(cid: int, request: Request):
    ch = db.get_channel(cid)
    if not ch:
        raise HTTPException(404, "Canal inconnu.")
    if ch["type"] == "telegram" and ch["config"].get("bot_token"):
        base_url = str(request.base_url).rstrip("/")
        await notify.register_telegram_webhook(ch["config"]["bot_token"], cid, base_url)
    return {"result": await notify.send_test(ch)}


@app.get("/api/agents/{agent_id}/channels")
def agent_channels_get(agent_id: int):
    if not db.get_agent(agent_id):
        raise HTTPException(404, "Agent inconnu.")
    return [_mask_channel(ch) for ch in db.get_agent_channels(agent_id)]


@app.put("/api/agents/{agent_id}/channels")
def agent_channels_put(agent_id: int, body: list[AgentChannelEntry]):
    if not db.get_agent(agent_id):
        raise HTTPException(404, "Agent inconnu.")
    for entry in body:
        if db.get_channel(entry.channel_id):
            db.set_agent_channel(agent_id, entry.channel_id, entry.use_notifs, entry.use_questions)
    return [_mask_channel(ch) for ch in db.get_agent_channels(agent_id)]


@app.post("/api/webhooks/telegram/{cid}")
async def telegram_webhook(cid: int, request: Request):
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}
    msg = update.get("message") or update.get("edited_message") or {}
    reply_to = msg.get("reply_to_message") or {}
    if reply_to:
        reply_to_id = reply_to.get("message_id")
        text = (msg.get("text") or "").strip()
        if reply_to_id and text:
            notif = db.find_notification_by_telegram(cid, reply_to_id)
            if notif:
                db.answer_notification(notif["id"], text)
                if not db.open_questions(notif["agent_id"]):
                    db.advance_planned_sessions(notif["agent_id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@app.get("/api/timeline")
def timeline_get(profile_id: int | None = None):
    from datetime import datetime, timedelta, timezone
    now_ts = datetime.now(timezone.utc)
    cutoff = (now_ts - timedelta(hours=12)).isoformat(timespec="seconds")
    fwd    = (now_ts + timedelta(hours=6)).isoformat(timespec="seconds")
    pf = "AND (a.profile_id = ? OR a.profile_id IS NULL)" if profile_id else ""
    p_past = tuple(v for v in [cutoff, profile_id] if v is not None)
    p_plan = tuple(v for v in [fwd, profile_id] if v is not None)
    recent = db.query(
        f"SELECT s.id, s.agent_id, a.name AS agent_name, COALESCE(a.category,'') AS agent_category, s.status, "
        f"s.started_at, s.ended_at, s.objective "
        f"FROM sessions s JOIN agents a ON a.id = s.agent_id "
        f"WHERE s.status IN ('completed','failed','interrupted','running') "
        f"AND s.started_at >= ? {pf} ORDER BY s.started_at ASC LIMIT 80", p_past)
    planned = db.query(
        f"SELECT s.id, s.agent_id, a.name AS agent_name, COALESCE(a.category,'') AS agent_category, s.status, "
        f"s.scheduled_at AS started_at, NULL AS ended_at, s.objective "
        f"FROM sessions s JOIN agents a ON a.id = s.agent_id "
        f"WHERE s.status = 'planned' AND s.scheduled_at <= ? {pf} "
        f"ORDER BY s.scheduled_at ASC LIMIT 20", p_plan)
    return {"sessions": recent + planned}


# Front statique — monté en dernier
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
