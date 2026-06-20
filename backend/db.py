"""Accès SQLite (synchrone, protégé par verrou — les appels sont courts)."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Modèles Claude proposés par défaut pour un provider Anthropic neuf.
DEFAULT_ANTHROPIC_MODELS = ("claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5")

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mission_prompt TEXT NOT NULL,
    model       TEXT NOT NULL,
    effort      TEXT NOT NULL DEFAULT 'high',
    status      TEXT NOT NULL DEFAULT 'idle',          -- idle | running | paused
    category    TEXT NOT NULL DEFAULT '',              -- thème de regroupement (libre)
    max_iterations INTEGER NOT NULL DEFAULT 60,
    session_token_budget INTEGER NOT NULL DEFAULT 0,
    profile_id  INTEGER REFERENCES profiles(id),       -- NULL = agent système (visible tous profils)
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id     INTEGER NOT NULL REFERENCES agents(id),
    number       INTEGER NOT NULL,
    objective    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'planned',      -- planned | running | completed | failed | interrupted
    scheduled_at TEXT,
    started_at   TEXT,
    ended_at     TEXT,
    report       TEXT,
    deliverables TEXT,
    next_objective TEXT,
    error        TEXT,
    provider     TEXT,
    user_note    TEXT,                                  -- note de l'utilisateur (lancement manuel)
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    mission     TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    plan        TEXT,                                   -- plan JSON proposé par le superviseur
    status      TEXT NOT NULL DEFAULT 'proposed',       -- proposed | running | completed | archived
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agents(id),
    origin      TEXT NOT NULL,                          -- 'user' | 'agent:<nom>' | 'supervisor'
    description TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    project_id  INTEGER,
    depends_on  TEXT NOT NULL DEFAULT '[]',             -- liste JSON d'ids de tâches prérequises
    status      TEXT NOT NULL DEFAULT 'pending',        -- pending | in_progress | done | failed
    result      TEXT,
    session_id  INTEGER,
    created_at  TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent  TEXT NOT NULL,
    to_agent_id INTEGER NOT NULL REFERENCES agents(id),
    content     TEXT NOT NULL,
    read        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    ts         TEXT NOT NULL,
    type       TEXT NOT NULL,    -- status | thinking | text | tool_use | tool_result | error | usage
    content    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id, id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agents(id),
    session_id  INTEGER,
    type        TEXT NOT NULL,                          -- alert | question
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',           -- open | answered | dismissed
    response    TEXT,
    delivered   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    answered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_status ON notifications(status, id);

CREATE TABLE IF NOT EXISTS resources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,                          -- shared | agent | task
    agent_id    INTEGER,
    task_id     INTEGER,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,                          -- file | note | link
    filename    TEXT,                                   -- chemin disque relatif à RESOURCES_DIR (kind=file)
    content     TEXT,                                   -- texte (note) ou URL (link)
    description TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT NOT NULL DEFAULT 'user',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_res_scope ON resources(scope, agent_id);

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    scope      TEXT NOT NULL,                           -- agent | task
    task_id    INTEGER,
    mkey       TEXT NOT NULL,
    mvalue     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_id, scope, task_id, mkey)
);
CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent_id, scope);

CREATE TABLE IF NOT EXISTS providers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    ptype           TEXT NOT NULL DEFAULT 'anthropic',   -- anthropic | openai
    base_url        TEXT NOT NULL DEFAULT '',
    api_key         TEXT NOT NULL DEFAULT '',
    default_model   TEXT NOT NULL DEFAULT '',
    models          TEXT NOT NULL DEFAULT '[]',          -- liste JSON des modèles proposés
    native_features INTEGER NOT NULL DEFAULT 1,          -- (anthropic) thinking/effort/compaction/cache
    is_default      INTEGER NOT NULL DEFAULT 0,
    limit_short_tokens INTEGER NOT NULL DEFAULT 0,       -- plafond court terme (tokens)
    limit_short_hours  INTEGER NOT NULL DEFAULT 0,       -- fenêtre court terme (heures)
    limit_long_tokens  INTEGER NOT NULL DEFAULT 0,       -- plafond long terme (tokens)
    limit_long_days    INTEGER NOT NULL DEFAULT 0,       -- fenêtre long terme (jours)
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    name       TEXT NOT NULL,
    port       INTEGER,
    command    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'running',         -- running | stopped
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_svc_agent ON services(agent_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes manquantes aux bases créées avant les évolutions du schéma."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    for col, ddl in (("title", "TEXT NOT NULL DEFAULT ''"),
                     ("project_id", "INTEGER"),
                     ("depends_on", "TEXT NOT NULL DEFAULT '[]'"),
                     ("input_tokens", "INTEGER NOT NULL DEFAULT 0"),
                     ("output_tokens", "INTEGER NOT NULL DEFAULT 0")):
        if col not in cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)")}
    if "session_token_budget" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN session_token_budget INTEGER NOT NULL DEFAULT 0")
    if "provider_id" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN provider_id INTEGER")
    if "category" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN category TEXT NOT NULL DEFAULT ''")
    if "profile_id" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN profile_id INTEGER REFERENCES profiles(id)")
        # Migration : créer le profil Par défaut et y rattacher tous les agents existants.
        ts = now()
        conn.execute("INSERT OR IGNORE INTO profiles (name, created_at) VALUES ('Par défaut', ?)", (ts,))
        default_pid = conn.execute("SELECT id FROM profiles WHERE name = 'Par défaut'").fetchone()["id"]
        conn.execute("UPDATE agents SET profile_id = ? WHERE profile_id IS NULL", (default_pid,))
    pjcols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "profile_id" not in pjcols:
        conn.execute("ALTER TABLE projects ADD COLUMN profile_id INTEGER REFERENCES profiles(id)")
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "user_note" not in scols:
        conn.execute("ALTER TABLE sessions ADD COLUMN user_note TEXT")
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(providers)")}
    for col in ("limit_short_tokens", "limit_short_hours", "limit_long_tokens", "limit_long_days"):
        if col not in pcols:
            conn.execute(f"ALTER TABLE providers ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    if "models" not in pcols:
        conn.execute("ALTER TABLE providers ADD COLUMN models TEXT NOT NULL DEFAULT '[]'")
        # Amorce : pour les providers anthropic existants sans liste, propose les modèles Claude courants.
        for p in conn.execute("SELECT id, ptype, default_model FROM providers"):
            models = list(DEFAULT_ANTHROPIC_MODELS) if p["ptype"] == "anthropic" else []
            if p["default_model"] and p["default_model"] not in models:
                models.insert(0, p["default_model"])
            conn.execute("UPDATE providers SET models = ? WHERE id = ?",
                         (json.dumps(models), p["id"]))
    _seed_providers(conn)
    conn.commit()


def _seed_providers(conn: sqlite3.Connection) -> None:
    """Crée le(s) provider(s) initiaux : migre les anciens réglages primaire/fallback s'ils existent,
    sinon un provider Anthropic par défaut (clé via ANTHROPIC_API_KEY)."""
    if conn.execute("SELECT COUNT(*) AS c FROM providers").fetchone()["c"]:
        return
    ts = now()

    def setting(key):
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else {}

    p = setting("primary_provider")
    a_models = list(DEFAULT_ANTHROPIC_MODELS)
    if config.DEFAULT_MODEL not in a_models:
        a_models.insert(0, config.DEFAULT_MODEL)
    conn.execute(
        "INSERT INTO providers (name, ptype, base_url, api_key, default_model, models, native_features, is_default, created_at) "
        "VALUES (?, 'anthropic', ?, ?, ?, ?, ?, 1, ?)",
        ("Anthropic" if not p.get("base_url") else "Primaire (migré)",
         p.get("base_url", ""), p.get("api_key", ""), config.DEFAULT_MODEL, json.dumps(a_models),
         1 if p.get("native_features", True) else 0, ts))
    f = setting("fallback_provider")
    if f.get("enabled") and f.get("base_url"):
        conn.execute(
            "INSERT INTO providers (name, ptype, base_url, api_key, default_model, native_features, is_default, created_at) "
            "VALUES (?, 'openai', ?, ?, ?, 0, 0, ?)",
            ("Secours (migré)", f.get("base_url", ""), f.get("api_key", ""), f.get("model", ""), ts))


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
        _migrate(_conn)
    return _conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        cur = get_conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


# ---------- Agents ----------

def create_agent(name, description, mission_prompt, model, effort, max_iterations,
                 session_token_budget=0, provider_id=None, category="", profile_id=None) -> int:
    return execute(
        "INSERT INTO agents (name, description, mission_prompt, model, effort, max_iterations, "
        "session_token_budget, provider_id, category, profile_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, description, mission_prompt, model, effort, max_iterations, session_token_budget,
         provider_id, category, profile_id, now()),
    )


def get_agent(agent_id: int) -> dict | None:
    return query_one("SELECT * FROM agents WHERE id = ?", (agent_id,))


def get_agent_by_name(name: str) -> dict | None:
    return query_one("SELECT * FROM agents WHERE name = ?", (name,))


def list_agents(profile_id: int | None = None) -> list[dict]:
    """Retourne les agents du profil donné + les agents système (profile_id IS NULL).
    Sans profil, retourne tous les agents."""
    if profile_id is not None:
        return query("SELECT * FROM agents WHERE profile_id = ? OR profile_id IS NULL ORDER BY id",
                     (profile_id,))
    return query("SELECT * FROM agents ORDER BY id")


# ---------- Profils ----------

def list_profiles() -> list[dict]:
    return query("SELECT p.*, COUNT(a.id) AS agents_count FROM profiles p "
                 "LEFT JOIN agents a ON a.profile_id = p.id GROUP BY p.id ORDER BY p.id")


def create_profile(name: str) -> int:
    return execute("INSERT INTO profiles (name, created_at) VALUES (?, ?)", (name, now()))


def get_profile(pid: int) -> dict | None:
    return query_one("SELECT * FROM profiles WHERE id = ?", (pid,))


def delete_profile(pid: int) -> None:
    execute("UPDATE agents SET profile_id = NULL WHERE profile_id = ?", (pid,))
    execute("DELETE FROM profiles WHERE id = ?", (pid,))


def set_agent_status(agent_id: int, status: str) -> None:
    execute("UPDATE agents SET status = ? WHERE id = ?", (status, agent_id))


def delete_agent(agent_id: int) -> None:
    """Supprime un agent et tout son historique. Les tâches de mission sont conservées
    (historique des missions) ; les tâches hors mission sont supprimées."""
    execute("DELETE FROM events WHERE agent_id = ?", (agent_id,))
    execute("DELETE FROM sessions WHERE agent_id = ?", (agent_id,))
    execute("DELETE FROM messages WHERE to_agent_id = ?", (agent_id,))
    execute("DELETE FROM notifications WHERE agent_id = ?", (agent_id,))
    execute("DELETE FROM memories WHERE agent_id = ?", (agent_id,))
    execute("DELETE FROM services WHERE agent_id = ?", (agent_id,))
    execute("DELETE FROM tasks WHERE agent_id = ? AND project_id IS NULL", (agent_id,))
    execute("DELETE FROM agents WHERE id = ?", (agent_id,))


# ---------- Sessions ----------

def create_session(agent_id, objective, scheduled_at) -> int:
    n = query_one("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM sessions WHERE agent_id = ?",
                  (agent_id,))["n"]
    return execute(
        "INSERT INTO sessions (agent_id, number, objective, status, scheduled_at) "
        "VALUES (?, ?, ?, 'planned', ?)",
        (agent_id, n, objective, scheduled_at or now()),
    )


def get_session(session_id: int) -> dict | None:
    return query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))


def sessions_for_agent(agent_id, limit=50) -> list[dict]:
    return query("SELECT * FROM sessions WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
                 (agent_id, limit))


def due_sessions() -> list[dict]:
    return query(
        "SELECT s.* FROM sessions s JOIN agents a ON a.id = s.agent_id "
        "WHERE s.status = 'planned' AND s.scheduled_at <= ? AND a.status = 'idle' "
        "ORDER BY s.scheduled_at",
        (now(),),
    )


def running_sessions_count() -> int:
    return query_one("SELECT COUNT(*) AS c FROM sessions WHERE status = 'running'")["c"]


def update_session(session_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), session_id))


# ---------- Tâches ----------

def create_task(agent_id, origin, description, project_id=None, depends_on=None, title="") -> int:
    return execute(
        "INSERT INTO tasks (agent_id, origin, description, title, project_id, depends_on, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_id, origin, description, title, project_id,
         json.dumps(depends_on or []), now()),
    )


def get_task(task_id: int) -> dict | None:
    return query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))


def pending_tasks(agent_id: int) -> list[dict]:
    return query("SELECT * FROM tasks WHERE agent_id = ? AND status = 'pending' ORDER BY id", (agent_id,))


def _deps_done(depends_on_json: str | None) -> bool:
    ids = json.loads(depends_on_json or "[]")
    if not ids:
        return True
    placeholders = ",".join("?" * len(ids))
    rows = query(f"SELECT status FROM tasks WHERE id IN ({placeholders})", tuple(ids))
    return bool(rows) and all(r["status"] == "done" for r in rows)


def ready_tasks(agent_id: int) -> list[dict]:
    """Tâches en attente dont toutes les dépendances sont terminées (prêtes à être exécutées)."""
    return [t for t in pending_tasks(agent_id) if _deps_done(t["depends_on"])]


def task_is_blocked(task: dict) -> bool:
    return task["status"] == "pending" and not _deps_done(task["depends_on"])


def dependency_results(task: dict) -> list[dict]:
    ids = json.loads(task["depends_on"] or "[]")
    return [t for t in (get_task(i) for i in ids) if t and t["status"] == "done"]


def tasks_for_agent(agent_id, limit=100) -> list[dict]:
    return query("SELECT * FROM tasks WHERE agent_id = ? ORDER BY id DESC LIMIT ?", (agent_id, limit))


def update_task(task_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))


# ---------- Messages inter-agents ----------

def send_message(from_agent, to_agent_id, content) -> int:
    return execute(
        "INSERT INTO messages (from_agent, to_agent_id, content, created_at) VALUES (?, ?, ?, ?)",
        (from_agent, to_agent_id, content, now()),
    )


def unread_messages(agent_id: int) -> list[dict]:
    return query("SELECT * FROM messages WHERE to_agent_id = ? AND read = 0 ORDER BY id", (agent_id,))


def mark_messages_read(ids: list[int]) -> None:
    if ids:
        placeholders = ",".join("?" * len(ids))
        execute(f"UPDATE messages SET read = 1 WHERE id IN ({placeholders})", tuple(ids))


# ---------- Événements ----------

def add_event(session_id, agent_id, type_, content) -> int:
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    return execute(
        "INSERT INTO events (session_id, agent_id, ts, type, content) VALUES (?, ?, ?, ?, ?)",
        (session_id, agent_id, now(), type_, content),
    )


def events_for_session(session_id, after_id=0, limit=500) -> list[dict]:
    return query("SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id LIMIT ?",
                 (session_id, after_id, limit))


# ---------- Réglages ----------

def get_setting(key: str, default=None):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    execute("INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)))


# ---------- Providers LLM ----------

def create_provider(name, ptype, base_url, api_key, default_model, native_features, is_default=False,
                    limit_short_tokens=0, limit_short_hours=0, limit_long_tokens=0, limit_long_days=0,
                    models=None) -> int:
    pid = execute(
        "INSERT INTO providers (name, ptype, base_url, api_key, default_model, models, native_features, is_default, "
        "limit_short_tokens, limit_short_hours, limit_long_tokens, limit_long_days, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
        (name, ptype, base_url, api_key, default_model, json.dumps(models or []),
         1 if native_features else 0,
         limit_short_tokens, limit_short_hours, limit_long_tokens, limit_long_days, now()))
    if is_default:
        set_default_provider(pid)
    return pid


def provider_usage(provider_name: str, hours: float) -> int:
    """Total de tokens (in+out) consommés par un provider sur les `hours` dernières heures,
    rattachés à la fin de session (à défaut, au démarrage)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    row = query_one(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS used FROM sessions "
        "WHERE provider = ? AND COALESCE(ended_at, started_at) >= ?", (provider_name, cutoff))
    return row["used"]


def get_provider(pid: int) -> dict | None:
    return query_one("SELECT * FROM providers WHERE id = ?", (pid,))


def get_provider_by_name(name: str) -> dict | None:
    return query_one("SELECT * FROM providers WHERE name = ?", (name,))


def list_providers() -> list[dict]:
    return query("SELECT * FROM providers ORDER BY is_default DESC, id")


def update_provider(pid: int, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE providers SET {cols} WHERE id = ?", (*fields.values(), pid))


def set_default_provider(pid: int) -> None:
    execute("UPDATE providers SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (pid,))


def default_provider() -> dict | None:
    return query_one("SELECT * FROM providers ORDER BY is_default DESC, id LIMIT 1")


def delete_provider(pid: int) -> None:
    execute("UPDATE agents SET provider_id = NULL WHERE provider_id = ?", (pid,))
    execute("DELETE FROM providers WHERE id = ?", (pid,))
    if not query_one("SELECT id FROM providers WHERE is_default = 1"):
        first = query_one("SELECT id FROM providers ORDER BY id LIMIT 1")
        if first:
            set_default_provider(first["id"])


def provider_in_use(pid: int) -> int:
    return query_one("SELECT COUNT(*) AS c FROM agents WHERE provider_id = ?", (pid,))["c"]


# ---------- Statistiques de tokens ----------

def _since_cutoff(period: str | None) -> str | None:
    """Retourne un timestamp ISO cutoff selon la période ('24h', '7d', None=tout)."""
    if period == "24h":
        return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    if period == "7d":
        return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    return None


def tokens_by_agent(period: str | None = None, profile_id: int | None = None) -> list[dict]:
    since = _since_cutoff(period)
    date_filter = "AND COALESCE(s.started_at,'') >= ?" if since else ""
    prof_filter = "AND (a.profile_id = ? OR a.profile_id IS NULL)" if profile_id else ""
    params = tuple(v for v in (since, profile_id) if v is not None)
    return query(
        f"SELECT a.id, a.name, COALESCE(SUM(s.input_tokens),0) AS input_tokens, "
        f"COALESCE(SUM(s.output_tokens),0) AS output_tokens, COUNT(s.id) AS sessions "
        f"FROM agents a LEFT JOIN sessions s ON s.agent_id = a.id {date_filter} "
        f"WHERE 1=1 {prof_filter} "
        f"GROUP BY a.id ORDER BY input_tokens + output_tokens DESC", params)


def tokens_by_provider(period: str | None = None, profile_id: int | None = None) -> list[dict]:
    since = _since_cutoff(period)
    date_filter = "AND COALESCE(started_at,'') >= ?" if since else ""
    prof_filter = ("AND agent_id IN (SELECT id FROM agents WHERE profile_id = ? OR profile_id IS NULL)"
                   if profile_id else "")
    params = tuple(v for v in (since, profile_id) if v is not None)
    return query(
        f"SELECT COALESCE(provider, '(inconnu)') AS provider, SUM(input_tokens) AS input_tokens, "
        f"SUM(output_tokens) AS output_tokens, COUNT(*) AS sessions "
        f"FROM sessions WHERE input_tokens + output_tokens > 0 {date_filter} {prof_filter} "
        f"GROUP BY COALESCE(provider, '(inconnu)') ORDER BY input_tokens + output_tokens DESC", params)


def tokens_by_project(period: str | None = None, profile_id: int | None = None) -> list[dict]:
    since = _since_cutoff(period)
    date_filter = "AND COALESCE(s2.started_at,'') >= ?" if since else ""
    prof_filter = ("AND (t.agent_id IS NULL OR t.agent_id IN "
                   "(SELECT id FROM agents WHERE profile_id = ? OR profile_id IS NULL))"
                   if profile_id else "")
    params = tuple(v for v in (since, profile_id) if v is not None)
    return query(
        f"SELECT p.id, p.title, p.status, COALESCE(SUM(t.input_tokens),0) AS input_tokens, "
        f"COALESCE(SUM(t.output_tokens),0) AS output_tokens "
        f"FROM projects p LEFT JOIN tasks t ON t.project_id = p.id "
        f"LEFT JOIN sessions s2 ON s2.id = t.session_id "
        f"WHERE 1=1 {date_filter} {prof_filter} "
        f"GROUP BY p.id ORDER BY p.id DESC", params)


def add_task_tokens(task_id: int, input_tokens: int, output_tokens: int) -> None:
    execute("UPDATE tasks SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ? "
            "WHERE id = ?", (input_tokens, output_tokens, task_id))


def tokens_summary(period: str | None = None, profile_id: int | None = None) -> dict:
    """Totaux, moyennes et extrêmes sur les sessions ayant consommé des tokens."""
    since = _since_cutoff(period)
    date_filter = "AND COALESCE(started_at,'') >= ?" if since else ""
    prof_filter = ("AND agent_id IN (SELECT id FROM agents WHERE profile_id = ? OR profile_id IS NULL)"
                   if profile_id else "")
    params = tuple(v for v in (since, profile_id) if v is not None)
    row = query_one(
        f"SELECT COUNT(*) AS sessions, COALESCE(SUM(input_tokens),0) AS input_tokens, "
        f"COALESCE(SUM(output_tokens),0) AS output_tokens, "
        f"COALESCE(MAX(input_tokens + output_tokens),0) AS max_session "
        f"FROM sessions WHERE input_tokens + output_tokens > 0 {date_filter} {prof_filter}", params)
    completed = query_one(
        f"SELECT COUNT(*) AS c FROM sessions WHERE status = 'completed' {date_filter} {prof_filter}", params)["c"]
    total = row["input_tokens"] + row["output_tokens"]
    row["total"] = total
    row["avg_session"] = round(total / row["sessions"]) if row["sessions"] else 0
    row["completed_sessions"] = completed
    # Tokens des dernières 24h (toujours inclus pour les KPIs du dashboard)
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    p24 = (cutoff_24h, profile_id) if profile_id else (cutoff_24h,)
    r24 = query_one(
        "SELECT COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens "
        f"FROM sessions WHERE COALESCE(started_at,'') >= ? {prof_filter}", p24)
    row["last_24h"] = {"input_tokens": r24["input_tokens"], "output_tokens": r24["output_tokens"]}
    return row


def tokens_by_day(days: int = 30, period: str | None = None, profile_id: int | None = None) -> list[dict]:
    """Consommation agrégée par jour (sur la date de démarrage des sessions)."""
    since = _since_cutoff(period)
    date_filter = "AND started_at >= ?" if since else ""
    prof_filter = ("AND agent_id IN (SELECT id FROM agents WHERE profile_id = ? OR profile_id IS NULL)"
                   if profile_id else "")
    params_day = tuple(v for v in (since, profile_id, days) if v is not None)
    return query(
        f"SELECT substr(started_at, 1, 10) AS day, SUM(input_tokens) AS input_tokens, "
        f"SUM(output_tokens) AS output_tokens, COUNT(*) AS sessions "
        f"FROM sessions WHERE started_at IS NOT NULL AND input_tokens + output_tokens > 0 {date_filter} {prof_filter} "
        f"GROUP BY day ORDER BY day DESC LIMIT ?", params_day)


def tokens_by_category(period: str | None = None, profile_id: int | None = None) -> list[dict]:
    """Consommation agrégée par thème d'agent."""
    since = _since_cutoff(period)
    date_filter = "AND COALESCE(s.started_at,'') >= ?" if since else ""
    prof_filter = "AND (a.profile_id = ? OR a.profile_id IS NULL)" if profile_id else ""
    params = tuple(v for v in (since, profile_id) if v is not None)
    return query(
        f"SELECT CASE WHEN a.category = '' THEN '(sans thème)' ELSE a.category END AS category, "
        f"COALESCE(SUM(s.input_tokens),0) AS input_tokens, COALESCE(SUM(s.output_tokens),0) AS output_tokens, "
        f"COUNT(s.id) AS sessions FROM agents a LEFT JOIN sessions s ON s.agent_id = a.id "
        f"WHERE 1=1 {date_filter} {prof_filter} "
        f"GROUP BY a.category HAVING input_tokens + output_tokens > 0 "
        f"ORDER BY input_tokens + output_tokens DESC", params)


def agent_categories() -> list[str]:
    rows = query("SELECT DISTINCT category FROM agents WHERE category != '' ORDER BY category")
    return [r["category"] for r in rows]


# ---------- Notifications / sollicitations ----------

def create_notification(agent_id, session_id, type_, content) -> int:
    return execute(
        "INSERT INTO notifications (agent_id, session_id, type, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (agent_id, session_id, type_, content, now()),
    )


def get_notification(nid: int) -> dict | None:
    return query_one("SELECT * FROM notifications WHERE id = ?", (nid,))


def list_notifications(status: str | None = None, agent_id: int | None = None,
                       type_: str | None = None) -> list[dict]:
    clauses, params = [], []
    if status:
        clauses.append("n.status = ?"); params.append(status)
    if agent_id is not None:
        clauses.append("n.agent_id = ?"); params.append(agent_id)
    if type_:
        clauses.append("n.type = ?"); params.append(type_)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return query(f"SELECT n.*, a.name AS agent_name FROM notifications n "
                 f"JOIN agents a ON a.id = n.agent_id {where} ORDER BY n.id DESC LIMIT 200",
                 tuple(params))


def answer_notification(nid: int, response: str) -> None:
    execute("UPDATE notifications SET status = 'answered', response = ?, answered_at = ? WHERE id = ?",
            (response, now(), nid))


def dismiss_notification(nid: int) -> None:
    execute("UPDATE notifications SET status = 'dismissed' WHERE id = ?", (nid,))


def open_notifications_count() -> int:
    return query_one("SELECT COUNT(*) AS c FROM notifications WHERE status = 'open'")["c"]


def undelivered_answers(agent_id: int) -> list[dict]:
    return query("SELECT * FROM notifications WHERE agent_id = ? AND type = 'question' "
                 "AND status = 'answered' AND delivered = 0 ORDER BY id", (agent_id,))


def open_questions(agent_id: int) -> list[dict]:
    return query("SELECT * FROM notifications WHERE agent_id = ? AND type = 'question' "
                 "AND status = 'open' ORDER BY id", (agent_id,))


def agents_awaiting() -> set[int]:
    """Agents en attente d'une réponse humaine (au moins une question ouverte)."""
    return {r["agent_id"] for r in query(
        "SELECT DISTINCT agent_id FROM notifications WHERE type = 'question' AND status = 'open'")}


def agents_with_undelivered_answers() -> list[int]:
    return [r["agent_id"] for r in query(
        "SELECT DISTINCT agent_id FROM notifications "
        "WHERE type = 'question' AND status = 'answered' AND delivered = 0")]


def advance_planned_sessions(agent_id: int) -> None:
    """Avance à maintenant les sessions planifiées d'un agent (reprise prompte après réponse)."""
    execute("UPDATE sessions SET scheduled_at = ? WHERE agent_id = ? AND status = 'planned' AND scheduled_at > ?",
            (now(), agent_id, now()))


def mark_notification_delivered(nid: int) -> None:
    execute("UPDATE notifications SET delivered = 1 WHERE id = ?", (nid,))


# ---------- Ressources ----------

def create_resource(scope, agent_id, task_id, name, kind, filename, content, description, size, created_by) -> int:
    return execute(
        "INSERT INTO resources (scope, agent_id, task_id, name, kind, filename, content, description, size, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (scope, agent_id, task_id, name, kind, filename, content, description, size, created_by, now()),
    )


def get_resource(rid: int) -> dict | None:
    return query_one("SELECT * FROM resources WHERE id = ?", (rid,))


def delete_resource(rid: int) -> None:
    execute("DELETE FROM resources WHERE id = ?", (rid,))


def list_resources(scope=None, agent_id=None, task_id=None, project_id=None) -> list[dict]:
    clauses, params = [], []
    if scope:
        clauses.append("r.scope = ?"); params.append(scope)
    if agent_id is not None:
        # Liées à l'agent, ou liées à une de ses tâches.
        clauses.append("((r.scope = 'agent' AND r.agent_id = ?) "
                       "OR (r.scope = 'task' AND r.task_id IN (SELECT id FROM tasks WHERE agent_id = ?)))")
        params += [agent_id, agent_id]
    if task_id is not None:
        clauses.append("r.task_id = ?"); params.append(task_id)
    if project_id is not None:
        clauses.append("r.task_id IN (SELECT id FROM tasks WHERE project_id = ?)")
        params.append(project_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return query(f"SELECT r.* FROM resources r{where} ORDER BY r.id DESC", tuple(params))


def resources_for_agent(agent_id: int) -> list[dict]:
    """Ressources accessibles à un agent : mutualisées + les siennes + celles de ses tâches."""
    return query(
        "SELECT r.* FROM resources r "
        "WHERE r.scope = 'shared' "
        "   OR (r.scope = 'agent' AND r.agent_id = ?) "
        "   OR (r.scope = 'task' AND r.task_id IN (SELECT id FROM tasks WHERE agent_id = ?)) "
        "ORDER BY r.id DESC",
        (agent_id, agent_id),
    )


# ---------- Mémoire structurée ----------

def memory_upsert(agent_id, scope, task_id, key, value) -> None:
    execute(
        "INSERT INTO memories (agent_id, scope, task_id, mkey, mvalue, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(agent_id, scope, task_id, mkey) DO UPDATE SET mvalue = excluded.mvalue, updated_at = excluded.updated_at",
        (agent_id, scope, task_id, key, value, now()),
    )


def memory_get(agent_id, scope, task_id, key) -> dict | None:
    if task_id is None:
        return query_one("SELECT * FROM memories WHERE agent_id = ? AND scope = ? AND task_id IS NULL AND mkey = ?",
                         (agent_id, scope, key))
    return query_one("SELECT * FROM memories WHERE agent_id = ? AND scope = ? AND task_id = ? AND mkey = ?",
                     (agent_id, scope, task_id, key))


def memory_list(agent_id, scope=None) -> list[dict]:
    if scope:
        return query("SELECT * FROM memories WHERE agent_id = ? AND scope = ? ORDER BY mkey", (agent_id, scope))
    return query("SELECT * FROM memories WHERE agent_id = ? ORDER BY scope, mkey", (agent_id,))


def memory_delete(agent_id, scope, task_id, key) -> None:
    if task_id is None:
        execute("DELETE FROM memories WHERE agent_id = ? AND scope = ? AND task_id IS NULL AND mkey = ?",
                (agent_id, scope, key))
    else:
        execute("DELETE FROM memories WHERE agent_id = ? AND scope = ? AND task_id = ? AND mkey = ?",
                (agent_id, scope, task_id, key))


# ---------- Projets / missions ----------

def create_project(title, mission, summary, plan, profile_id=None) -> int:
    return execute(
        "INSERT INTO projects (title, mission, summary, plan, status, profile_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?)",
        (title, mission, summary, json.dumps(plan, ensure_ascii=False), profile_id, now(), now()),
    )


def get_project(pid: int) -> dict | None:
    return query_one("SELECT * FROM projects WHERE id = ?", (pid,))


def list_projects(include_archived=False, profile_id=None) -> list[dict]:
    clauses, params = [], []
    if not include_archived:
        clauses.append("status != 'archived'")
    if profile_id is not None:
        clauses.append("profile_id = ?")
        params.append(profile_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return query(f"SELECT * FROM projects {where} ORDER BY id DESC", tuple(params))


def update_project(pid: int, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE projects SET {cols} WHERE id = ?", (*fields.values(), pid))


def delete_project(pid: int) -> None:
    execute("DELETE FROM projects WHERE id = ?", (pid,))


def project_tasks(pid: int) -> list[dict]:
    # LEFT JOIN : les tâches d'un agent supprimé restent visibles dans l'historique de la mission.
    return query("SELECT t.*, COALESCE(a.name, '(agent supprimé)') AS agent_name "
                 "FROM tasks t LEFT JOIN agents a ON a.id = t.agent_id "
                 "WHERE t.project_id = ? ORDER BY t.id", (pid,))


def cancel_downstream(failed_task_id: int, project_id: int) -> list[dict]:
    """Annule en cascade les tâches (transitivement) dépendantes d'une tâche échouée."""
    blocked = {failed_task_id}
    cancelled: list[dict] = []
    changed = True
    while changed:
        changed = False
        for t in project_tasks(project_id):
            if t["status"] != "pending" or t["id"] in blocked:
                continue
            deps = json.loads(t["depends_on"] or "[]")
            if any(d in blocked for d in deps):
                update_task(t["id"], status="cancelled",
                            result="Annulée : une tâche prérequise a échoué.", completed_at=now())
                blocked.add(t["id"])
                cancelled.append(t)
                changed = True
    return cancelled


def refresh_project_status(pid: int) -> str | None:
    """Met à jour le statut d'un projet en cours selon ses tâches. Renvoie le nouveau statut s'il a changé."""
    proj = get_project(pid)
    if not proj or proj["status"] != "running":
        return None
    tasks = project_tasks(pid)
    if not tasks or any(t["status"] in ("pending", "in_progress") for t in tasks):
        return None
    new_status = "needs_attention" if any(t["status"] in ("failed", "cancelled") for t in tasks) else "completed"
    update_project(pid, status=new_status)
    return new_status


# ---------- Registre de services ----------

def register_service(agent_id, name, port, command, notes) -> int:
    existing = query_one("SELECT id FROM services WHERE agent_id = ? AND name = ?", (agent_id, name))
    if existing:
        execute("UPDATE services SET port = ?, command = ?, notes = ?, status = 'running', updated_at = ? "
                "WHERE id = ?", (port, command, notes, now(), existing["id"]))
        return existing["id"]
    return execute(
        "INSERT INTO services (agent_id, name, port, command, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_id, name, port, command, notes, now(), now()),
    )


def list_services(status=None) -> list[dict]:
    if status:
        return query("SELECT s.*, a.name AS agent_name FROM services s JOIN agents a ON a.id = s.agent_id "
                     "WHERE s.status = ? ORDER BY s.id", (status,))
    return query("SELECT s.*, a.name AS agent_name FROM services s JOIN agents a ON a.id = s.agent_id "
                 "ORDER BY s.id")


def services_for_agent(agent_id: int) -> list[dict]:
    return query("SELECT * FROM services WHERE agent_id = ? ORDER BY id", (agent_id,))


def port_in_use(port: int) -> dict | None:
    return query_one("SELECT s.*, a.name AS agent_name FROM services s JOIN agents a ON a.id = s.agent_id "
                     "WHERE s.port = ? AND s.status = 'running' LIMIT 1", (port,))


def set_service_status(service_id, agent_id, status) -> None:
    execute("UPDATE services SET status = ?, updated_at = ? WHERE id = ? AND agent_id = ?",
            (status, now(), service_id, agent_id))
