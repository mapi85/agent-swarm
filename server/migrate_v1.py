"""Migration des données v1 (SQLite) → v2 (PostgreSQL), ids préservés.

Usage (dans le conteneur v2) :
    python -m server.migrate_v1 /chemin/vers/v1.db [--force]

- Idempotent au sens « cible vide » : refuse de tourner si des données existent,
  sauf --force (qui TRUNCATE toutes les tables métier d'abord).
- Préserve les ids partout (workdirs et fichiers ressources encodent l'id).
- profils → comptes (« Par défaut » = admin) ; agents sans profil = système.
- références par nom → par id (session.provider, tasks.origin, messages.from_agent).
- tasks.depends_on (JSON) → task_links ; sessions sans tâche → tâche « Sessions
  héritées (v1) » par agent, pour préserver l'historique consultable.
- mémoire scindée par utilisateur ; ressources scope=agent → scope=user.
- secrets (clés providers, config canaux, mot de passe SMTP) chiffrés à l'import.
- crée une ligne token_usage par session (continuité des stats/quotas).

Le mot de passe des comptes migrés est aléatoire et imprimé dans le rapport
(à changer ensuite via l'interface). Les emails sont des placeholders sur le
domaine, à corriger dans l'admin.
"""
import json
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, insert, text

from .config import get_settings
from .crypto import encrypt_secret
from .models import (
    Agent, AppSetting, Base, Event, Memory, Message, Mission, Notification,
    NotificationChannel, Provider, Resource, Service, Session, Task, TaskLink,
    TokenUsage, User,
)
from .security import hash_password

EMAIL_DOMAIN = "agents.mapi85.fr"
DEFAULT_PROFILE = "Par défaut"

# Tables métier à vider avec --force (ordre inverse des dépendances)
_TRUNCATE = [
    "token_usage", "events", "task_links", "sessions", "messages", "notifications",
    "memories", "services", "resources", "tasks", "missions", "agents",
    "notification_channels", "providers", "app_settings", "password_resets",
    "user_tokens", "users",
]


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    return s or "profil"


def parse_dt(val):
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def jload(val, default):
    if val in (None, ""):
        return default
    try:
        return json.loads(val)
    except (ValueError, TypeError):
        return default


def migrate(sqlite_path: str, force: bool) -> None:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    engine = create_engine(get_settings().database_url)  # psycopg accepte l'URL en sync

    with engine.begin() as db:
        existing = db.execute(text("SELECT count(*) FROM users")).scalar()
        if existing and not force:
            print(f"ABANDON : la cible contient déjà {existing} utilisateur(s). "
                  "Relance avec --force pour tout écraser.")
            sys.exit(1)
        if force:
            db.execute(text("TRUNCATE " + ", ".join(_TRUNCATE) + " RESTART IDENTITY CASCADE"))

        report = {}

        # --- 1. profils → users ---
        profiles = src.execute("SELECT * FROM profiles").fetchall()
        user_of_profile = {}
        creds = []
        for p in profiles:
            is_admin = p["name"] == DEFAULT_PROFILE
            pw = secrets.token_urlsafe(9)
            email = f"{slug(p['name'])}@{EMAIL_DOMAIN}"
            db.execute(insert(User.__table__).values(
                id=p["id"], email=email, password_hash=hash_password(pw),
                display_name=p["name"], role="admin" if is_admin else "user",
                status="active", quota_short_tokens=0, quota_short_hours=0,
                quota_long_tokens=0, quota_long_days=0,
                created_at=parse_dt(p["created_at"]) or datetime.now(timezone.utc)))
            user_of_profile[p["id"]] = p["id"]
            creds.append((p["name"], email, pw, "admin" if is_admin else "user"))
        admin_uid = next((pid for pid, _ in user_of_profile.items()
                          if next(pp["name"] for pp in profiles if pp["id"] == pid) == DEFAULT_PROFILE),
                         profiles[0]["id"] if profiles else None)
        report["users"] = len(profiles)

        # --- 2. providers (clés chiffrées) ---
        provider_name_to_id = {}
        for pr in src.execute("SELECT * FROM providers").fetchall():
            db.execute(insert(Provider.__table__).values(
                id=pr["id"], name=pr["name"], ptype=pr["ptype"], base_url=pr["base_url"] or "",
                api_key_enc=encrypt_secret(pr["api_key"]) if pr["api_key"] else "",
                default_model=pr["default_model"] or "", models=jload(pr["models"], []),
                native_features=bool(pr["native_features"]), is_default=bool(pr["is_default"]),
                priority=pr["priority"] or 0, limit_short_tokens=pr["limit_short_tokens"] or 0,
                limit_short_hours=pr["limit_short_hours"] or 0, limit_long_tokens=pr["limit_long_tokens"] or 0,
                limit_long_days=pr["limit_long_days"] or 0,
                created_at=parse_dt(pr["created_at"]) or datetime.now(timezone.utc)))
            provider_name_to_id[pr["name"]] = pr["id"]
        report["providers"] = len(provider_name_to_id)

        # --- 3. agents ---
        agents = src.execute("SELECT * FROM agents").fetchall()
        agent_by_id = {a["id"]: a for a in agents}
        agent_name_to_id = {a["name"]: a["id"] for a in agents}

        def agent_owner(agent_row):
            pid = agent_row["profile_id"]
            return user_of_profile.get(pid) if pid else None  # None = agent système

        for a in agents:
            db.execute(insert(Agent.__table__).values(
                id=a["id"], owner_user_id=agent_owner(a), name=a["name"],
                description=a["description"] or "", mission_prompt=a["mission_prompt"],
                category=a["category"] or "", provider_id=a["provider_id"], model=a["model"],
                effort=a["effort"] or "high", max_iterations=a["max_iterations"] or 60,
                session_token_budget=a["session_token_budget"] or 0, max_parallel_tasks=1,
                paused=(a["status"] == "paused"),
                created_at=parse_dt(a["created_at"]) or datetime.now(timezone.utc)))
        report["agents"] = len(agents)

        # --- 4. projets → missions ---
        projects = src.execute("SELECT * FROM projects").fetchall()
        project_owner = {}
        for pj in projects:
            owner = user_of_profile.get(pj["profile_id"]) or admin_uid
            project_owner[pj["id"]] = owner
            db.execute(insert(Mission.__table__).values(
                id=pj["id"], owner_user_id=owner, title=pj["title"], mission=pj["mission"],
                summary=pj["summary"] or "", plan=jload(pj["plan"], None), status=pj["status"],
                input_tokens=0, output_tokens=0,
                created_at=parse_dt(pj["created_at"]) or datetime.now(timezone.utc),
                updated_at=parse_dt(pj["updated_at"]) or datetime.now(timezone.utc)))
        report["missions"] = len(projects)

        # --- 5. tasks ---
        tasks = src.execute("SELECT * FROM tasks").fetchall()

        def task_owner(t):
            if t["project_id"] and project_owner.get(t["project_id"]):
                return project_owner[t["project_id"]]
            a = agent_by_id.get(t["agent_id"])
            return (agent_owner(a) if a else None) or admin_uid

        for t in tasks:
            origin = t["origin"] or "user"
            if origin == "user":
                created_by, cby_agent = "user", None
            elif origin == "supervisor":
                created_by, cby_agent = "supervisor", None
            elif origin.startswith("agent:"):
                created_by, cby_agent = "agent", agent_name_to_id.get(origin[6:])
            else:
                created_by, cby_agent = "user", None
            db.execute(insert(Task.__table__).values(
                id=t["id"], mission_id=t["project_id"], agent_id=t["agent_id"],
                owner_user_id=task_owner(t), title=t["title"] or "", description=t["description"],
                result=t["result"], status=t["status"], created_by=created_by,
                created_by_agent_id=cby_agent, input_tokens=t["input_tokens"] or 0,
                output_tokens=t["output_tokens"] or 0,
                created_at=parse_dt(t["created_at"]) or datetime.now(timezone.utc),
                completed_at=parse_dt(t["completed_at"])))
        report["tasks"] = len(tasks)

        # --- 6. task_links (depends_on) ---
        task_ids = {t["id"] for t in tasks}
        links = 0
        for t in tasks:
            for dep in jload(t["depends_on"], []):
                if dep in task_ids and dep != t["id"]:
                    db.execute(insert(TaskLink.__table__).values(
                        task_id=t["id"], linked_task_id=dep, kind="depends_on"))
                    links += 1
        report["task_links"] = links

        # --- 7. tâches synthétiques « Sessions héritées » (pour sessions sans tâche) ---
        # session_id -> task_id (première tâche liée en v1)
        task_of_session = {}
        for t in tasks:
            if t["session_id"] and t["session_id"] not in task_of_session:
                task_of_session[t["session_id"]] = t["id"]

        max_task_id = max(task_ids) if task_ids else 0
        legacy_task_of_agent = {}
        next_legacy = max_task_id + 1
        sessions = src.execute("SELECT * FROM sessions").fetchall()
        agents_needing_legacy = {s["agent_id"] for s in sessions if s["id"] not in task_of_session}
        for aid in sorted(agents_needing_legacy):
            a = agent_by_id.get(aid)
            owner = (agent_owner(a) if a else None) or admin_uid
            db.execute(insert(Task.__table__).values(
                id=next_legacy, mission_id=None, agent_id=aid, owner_user_id=owner,
                title="Sessions héritées (v1)",
                description="Archive des sessions v1 sans tâche associée (historique consultable).",
                result=None, status="done", created_by="user", created_by_agent_id=None,
                input_tokens=0, output_tokens=0, created_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc)))
            legacy_task_of_agent[aid] = next_legacy
            next_legacy += 1
        report["legacy_tasks"] = len(legacy_task_of_agent)

        # --- 8. sessions + token_usage ---
        usage_rows = []
        for s in sessions:
            task_id = task_of_session.get(s["id"]) or legacy_task_of_agent.get(s["agent_id"])
            prov_id = provider_name_to_id.get(s["provider"]) if s["provider"] else None
            db.execute(insert(Session.__table__).values(
                id=s["id"], task_id=task_id, agent_id=s["agent_id"], number=s["number"] or 1,
                objective=s["objective"] or "", status=s["status"], scheduled_at=parse_dt(s["scheduled_at"]),
                started_at=parse_dt(s["started_at"]), ended_at=parse_dt(s["ended_at"]),
                report=s["report"], deliverables=jload(s["deliverables"], None), error=s["error"],
                provider_id=prov_id, user_note=s["user_note"], input_tokens=s["input_tokens"] or 0,
                output_tokens=s["output_tokens"] or 0))
            if s["input_tokens"] or s["output_tokens"]:
                a = agent_by_id.get(s["agent_id"])
                usage_rows.append({
                    "ts": parse_dt(s["ended_at"]) or parse_dt(s["started_at"]) or datetime.now(timezone.utc),
                    "user_id": (agent_owner(a) if a else None) or admin_uid, "provider_id": prov_id,
                    "agent_id": s["agent_id"], "task_id": task_id, "session_id": s["id"],
                    "input_tokens": s["input_tokens"] or 0, "output_tokens": s["output_tokens"] or 0})
        report["sessions"] = len(sessions)
        if usage_rows:
            db.execute(insert(TokenUsage.__table__), usage_rows)
        report["token_usage"] = len(usage_rows)

        # --- 9. events (par lots ; agent_id abandonné en v2) ---
        valid_session_ids = {s["id"] for s in sessions}
        batch, total_ev = [], 0
        for e in src.execute("SELECT * FROM events"):
            if e["session_id"] not in valid_session_ids:
                continue
            batch.append({"id": e["id"], "session_id": e["session_id"],
                          "ts": parse_dt(e["ts"]) or datetime.now(timezone.utc),
                          "type": e["type"], "content": e["content"]})
            if len(batch) >= 2000:
                db.execute(insert(Event.__table__), batch); total_ev += len(batch); batch = []
        if batch:
            db.execute(insert(Event.__table__), batch); total_ev += len(batch)
        report["events"] = total_ev

        # --- 10. messages ---
        agent_ids = set(agent_by_id)
        n_msg = 0
        for m in src.execute("SELECT * FROM messages"):
            if m["to_agent_id"] not in agent_ids:
                continue
            from_id = agent_name_to_id.get(m["from_agent"])
            db.execute(insert(Message.__table__).values(
                id=m["id"], from_agent_id=from_id, to_agent_id=m["to_agent_id"], task_id=None,
                content=m["content"], read=bool(m["read"]),
                created_at=parse_dt(m["created_at"]) or datetime.now(timezone.utc)))
            n_msg += 1
        report["messages"] = n_msg

        # --- 11. notifications (destinataire = propriétaire de l'agent) ---
        n_notif = 0
        for nt in src.execute("SELECT * FROM notifications"):
            a = agent_by_id.get(nt["agent_id"])
            uid = (agent_owner(a) if a else None) or admin_uid
            db.execute(insert(Notification.__table__).values(
                id=nt["id"], user_id=uid, agent_id=nt["agent_id"], task_id=None,
                session_id=nt["session_id"] if nt["session_id"] in valid_session_ids else None,
                type=nt["type"], status=nt["status"], content=nt["content"], response=nt["response"],
                external_ids=jload(nt["external_ids"], None), channel_dispatched=bool(nt["channel_dispatched"]),
                created_at=parse_dt(nt["created_at"]) or datetime.now(timezone.utc),
                answered_at=parse_dt(nt["answered_at"])))
            n_notif += 1
        report["notifications"] = n_notif

        # --- 12. canaux (globaux v1 → propriétaire admin, config chiffrée + secret) ---
        n_chan = 0
        for ch in src.execute("SELECT * FROM notification_channels"):
            cfg = jload(ch["config"], {})
            if ch["type"] == "telegram" and not cfg.get("secret_token"):
                cfg["secret_token"] = secrets.token_urlsafe(24)
            db.execute(insert(NotificationChannel.__table__).values(
                id=ch["id"], owner_user_id=admin_uid, name=ch["name"], type=ch["type"],
                config_enc=encrypt_secret(json.dumps(cfg, ensure_ascii=False)),
                use_for_alerts=True, use_for_questions=(ch["type"] == "telegram"),
                enabled=bool(ch["enabled"]),
                created_at=parse_dt(ch["created_at"]) or datetime.now(timezone.utc)))
            n_chan += 1
        report["channels"] = n_chan

        # --- 13. resources (agent → user) ---
        n_res = 0
        for r in src.execute("SELECT * FROM resources"):
            if r["scope"] == "shared":
                scope, owner, tid = "shared", None, None
            else:  # agent (ou task, absent en prod)
                a = agent_by_id.get(r["agent_id"])
                scope = "user"
                owner = (agent_owner(a) if a else None) or admin_uid
                tid = r["task_id"] if r["task_id"] in task_ids else None
                if tid:
                    scope = "task"
            db.execute(insert(Resource.__table__).values(
                id=r["id"], scope=scope, owner_user_id=owner, task_id=tid, name=r["name"],
                kind=r["kind"], filename=r["filename"], content=r["content"],
                description=r["description"] or "", size=r["size"] or 0,
                created_by=r["created_by"] or "user",
                created_at=parse_dt(r["created_at"]) or datetime.now(timezone.utc)))
            n_res += 1
        report["resources"] = n_res

        # --- 14. memories (scindées par utilisateur) ---
        n_mem = 0
        for m in src.execute("SELECT * FROM memories"):
            a = agent_by_id.get(m["agent_id"])
            if a is None:
                continue
            if m["scope"] == "task" and m["task_id"] in task_ids:
                uid = task_owner(next(t for t in tasks if t["id"] == m["task_id"]))
                tid = m["task_id"]
            else:
                uid = agent_owner(a) or admin_uid
                tid = None
            db.execute(insert(Memory.__table__).values(
                id=m["id"], agent_id=m["agent_id"], user_id=uid,
                scope=("task" if tid else "agent"), task_id=tid, mkey=m["mkey"], mvalue=m["mvalue"],
                updated_at=parse_dt(m["updated_at"]) or datetime.now(timezone.utc)))
            n_mem += 1
        report["memories"] = n_mem

        # --- 15. services ---
        n_svc = 0
        for s in src.execute("SELECT * FROM services"):
            if s["agent_id"] not in agent_ids:
                continue
            db.execute(insert(Service.__table__).values(
                id=s["id"], agent_id=s["agent_id"], name=s["name"], port=s["port"],
                command=s["command"] or "", status=s["status"] or "running", notes=s["notes"] or "",
                created_at=parse_dt(s["created_at"]) or datetime.now(timezone.utc),
                updated_at=parse_dt(s["updated_at"]) or datetime.now(timezone.utc)))
            n_svc += 1
        report["services"] = n_svc

        # --- 16. settings SMTP (mot de passe chiffré) ---
        row = src.execute("SELECT value FROM settings WHERE key='smtp_config'").fetchone()
        if row:
            cfg = jload(row["value"], {})
            if cfg.get("password"):
                cfg["password_enc"] = encrypt_secret(cfg.pop("password"))
            db.execute(insert(AppSetting.__table__).values(key="smtp_config", value=cfg))
            report["smtp"] = 1

        # --- 17. réalignement des séquences d'id ---
        for tbl in ["users", "providers", "agents", "missions", "tasks", "sessions",
                    "events", "token_usage", "messages", "notifications",
                    "notification_channels", "resources", "memories", "services"]:
            db.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {tbl}), 1))"))

    src.close()

    print("\n=== MIGRATION TERMINÉE ===")
    for k, v in report.items():
        print(f"  {k:16} : {v}")
    print("\n=== COMPTES CRÉÉS (mot de passe temporaire — à changer via l'interface) ===")
    for name, email, pw, role in creds:
        print(f"  [{role:5}] {name:12} -> {email}  mdp: {pw}")
    print("\nEmails placeholder : corrige-les dans Administration > Utilisateurs.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    if not args:
        print("Usage : python -m server.migrate_v1 /chemin/v1.db [--force]")
        sys.exit(1)
    migrate(args[0], "--force" in sys.argv)
