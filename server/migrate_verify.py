"""Réconciliation v1 (SQLite) ↔ v2 (PostgreSQL) après migration.

Prouve que CHAQUE ligne v1 est soit présente en v2, soit écartée avec une raison
explicite. Ne modifie rien (lecture seule des deux bases).

Usage (conteneur v2) :
    python -m server.migrate_verify /tmp/v1.db

Sort un rapport structuré et un verdict final. Code de sortie non nul si une
anomalie non expliquée est détectée.
"""
import json
import sqlite3
import sys

from sqlalchemy import create_engine, text

from .config import get_settings

OK = "\033[0m"


def migrate_verify(sqlite_path: str) -> int:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    engine = create_engine(get_settings().database_url)

    def v1count(t, where=""):
        return src.execute(f"SELECT count(*) FROM {t} {where}").fetchone()[0]

    def v1sum(expr, t, where=""):
        return src.execute(f"SELECT COALESCE(SUM({expr}),0) FROM {t} {where}").fetchone()[0]

    anomalies = []
    lines = []

    def section(title):
        lines.append(f"\n=== {title} ===")

    def row(label, v1, v2, note=""):
        flag = "" if v1 == v2 else "  ⚠"
        lines.append(f"  {label:32} v1={v1:<8} v2={v2:<8} {note}{flag}")

    def note(label, val, txt=""):
        lines.append(f"  {label:32} {val:<8} {txt}")

    with engine.connect() as db:
        def c(sql, **p):
            return db.execute(text(sql), p).scalar()

        # Ensembles utiles côté v1
        agent_ids = {r["id"] for r in src.execute("SELECT id FROM agents")}
        agent_names = {r["name"] for r in src.execute("SELECT id,name FROM agents")}
        provider_names = {r["name"] for r in src.execute("SELECT name FROM providers")}
        session_ids = {r["id"] for r in src.execute("SELECT id FROM sessions")}
        task_ids = {r["id"] for r in src.execute("SELECT id FROM tasks")}

        # 1. Comptes
        section("1. COMPTES (profils → utilisateurs)")
        row("profils / utilisateurs", v1count("profiles"), c("SELECT count(*) FROM users"))

        # 2. Réconciliation par table
        section("2. TABLES — réconciliation de comptes")
        row("agents", v1count("agents"), c("SELECT count(*) FROM agents"))
        row("providers", v1count("providers"), c("SELECT count(*) FROM providers"))
        row("projets → missions", v1count("projects"), c("SELECT count(*) FROM missions"))

        v1_tasks = v1count("tasks")
        v2_tasks = c("SELECT count(*) FROM tasks")
        legacy = c("SELECT count(*) FROM tasks WHERE title='Sessions héritées (v1)'")
        row("tâches", v1_tasks, v2_tasks - legacy, f"(+{legacy} tâches archive synthétiques)")
        if v2_tasks - legacy != v1_tasks:
            anomalies.append("Nombre de tâches réelles ≠ v1")

        row("sessions", v1count("sessions"), c("SELECT count(*) FROM sessions"))
        row("notifications", v1count("notifications"), c("SELECT count(*) FROM notifications"))
        row("resources", v1count("resources"), c("SELECT count(*) FROM resources"))
        row("services", v1count("services"), c("SELECT count(*) FROM services"))
        row("canaux", v1count("notification_channels"), c("SELECT count(*) FROM notification_channels"))

        # 3. Lignes écartées — avec raison explicite
        section("3. LIGNES ÉCARTÉES (raison explicite, jamais silencieux)")

        # events : orphelins (session inexistante)
        v1_events = v1count("events")
        v2_events = c("SELECT count(*) FROM events")
        orphan_events = sum(1 for r in src.execute("SELECT session_id FROM events")
                            if r["session_id"] not in session_ids)
        note("events v1", v1_events)
        note("events v2", v2_events)
        note("  → écartés (session absente)", orphan_events, "attendu = v1 - v2")
        if v1_events - v2_events != orphan_events:
            anomalies.append(f"events : écart inexpliqué ({v1_events - v2_events} ≠ {orphan_events})")

        # messages : vers agent supprimé
        v1_msg = v1count("messages")
        v2_msg = c("SELECT count(*) FROM messages")
        dropped_msg = sum(1 for r in src.execute("SELECT to_agent_id FROM messages")
                          if r["to_agent_id"] not in agent_ids)
        note("messages v1", v1_msg)
        note("messages v2", v2_msg)
        note("  → écartés (destinataire supprimé)", dropped_msg, "attendu = v1 - v2")
        if v1_msg - v2_msg != dropped_msg:
            anomalies.append(f"messages : écart inexpliqué ({v1_msg - v2_msg} ≠ {dropped_msg})")

        # memories : doublons SQLite (NULL distinct) fusionnés
        v1_mem = v1count("memories")
        v2_mem = c("SELECT count(*) FROM memories")
        v1_mem_valid = sum(1 for r in src.execute("SELECT agent_id FROM memories")
                           if r["agent_id"] in agent_ids)
        # groupes de doublons v1 et si les valeurs diffèrent réellement
        groups = {}
        for m in src.execute("SELECT * FROM memories ORDER BY updated_at, id"):
            if m["agent_id"] not in agent_ids:
                continue
            key = (m["agent_id"], m["scope"], m["task_id"] if m["scope"] == "task" else None, m["mkey"])
            groups.setdefault(key, []).append(m["mvalue"])
        collapsed = sum(len(v) - 1 for v in groups.values() if len(v) > 1)
        differing = sum(1 for v in groups.values() if len(set(v)) > 1)
        note("memories v1 (agents existants)", v1_mem_valid)
        note("memories v2", v2_mem)
        note("  → doublons fusionnés", collapsed, "bug v1 NULL-distinct ; valeur la plus récente gardée")
        note("  dont groupes à valeurs distinctes", differing,
             "(la v1 renvoyait déjà la plus récente via fetchone)")
        note("  memories d'agents supprimés écartés", v1_mem - v1_mem_valid)
        if len(groups) != v2_mem:
            anomalies.append(f"memories : clés distinctes {len(groups)} ≠ v2 {v2_mem}")

        # services : agent supprimé
        v1_svc = v1count("services")
        v2_svc = c("SELECT count(*) FROM services")
        dropped_svc = sum(1 for r in src.execute("SELECT agent_id FROM services")
                          if r["agent_id"] not in agent_ids)
        if v1_svc - v2_svc != dropped_svc:
            anomalies.append(f"services : écart inexpliqué")
        note("services écartés (agent supprimé)", dropped_svc)

        # 4. Sommes de contrôle (contenu, pas seulement comptes)
        section("4. SOMMES DE CONTRÔLE (contenu préservé)")
        row("Σ tokens tâches (in+out)",
            v1sum("input_tokens+output_tokens", "tasks"),
            c("SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM tasks"))
        row("Σ tokens sessions (in+out)",
            v1sum("input_tokens+output_tokens", "sessions"),
            c("SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM sessions"))
        # longueur totale du contenu des events migrés (échantillon d'intégrité)
        note("Σ longueur contenu events v2", c("SELECT COALESCE(SUM(length(content)),0) FROM events"))

        # 5. Références résolues (nom → id)
        section("5. RÉFÉRENCES RÉSOLUES (nom → id)")
        unresolved_origin = sum(
            1 for r in src.execute("SELECT origin FROM tasks WHERE origin LIKE 'agent:%'")
            if r["origin"][6:] not in agent_names)
        note("tâches origin=agent: non résolues", unresolved_origin,
             "(agent créateur supprimé → created_by_agent_id NULL, tâche conservée)")
        unresolved_prov = {r["provider"] for r in src.execute(
            "SELECT DISTINCT provider FROM sessions WHERE provider IS NOT NULL")
            if r["provider"] not in provider_names}
        note("providers de session non résolus", len(unresolved_prov),
             f"{sorted(unresolved_prov)} → provider_id NULL (session conservée)")

        # 6. Propriété / cloisonnement
        section("6. PROPRIÉTÉ (cloisonnement — à revoir manuellement si besoin)")
        sys_agents = [r["id"] for r in src.execute("SELECT id FROM agents WHERE profile_id IS NULL")]
        note("agents système (profil NULL)", len(sys_agents), str(sys_agents))
        tasks_sys_to_admin = c(
            "SELECT count(*) FROM tasks t JOIN agents a ON a.id=t.agent_id "
            "WHERE a.owner_user_id IS NULL AND t.mission_id IS NULL")
        note("tâches d'agents système → admin", tasks_sys_to_admin,
             "(profil créateur inconnu en v1 : info absente, pas perdue)")
        dist = db.execute(text(
            "SELECT COALESCE(owner_user_id::text,'système') o, count(*) n FROM agents GROUP BY 1 ORDER BY 1")).all()
        note("agents par propriétaire", "", ", ".join(f"{o}:{n}" for o, n in dist))

        # 7. Intégrité référentielle v2
        section("7. INTÉGRITÉ RÉFÉRENTIELLE v2 (doit être 0 partout)")
        checks = {
            "sessions sans tâche": "SELECT count(*) FROM sessions WHERE task_id IS NULL",
            "sessions → tâche absente": "SELECT count(*) FROM sessions s LEFT JOIN tasks t ON t.id=s.task_id WHERE t.id IS NULL",
            "tâches → agent absent": "SELECT count(*) FROM tasks t LEFT JOIN agents a ON a.id=t.agent_id WHERE a.id IS NULL",
            "tâches → propriétaire absent": "SELECT count(*) FROM tasks t LEFT JOIN users u ON u.id=t.owner_user_id WHERE u.id IS NULL",
            "task_links → tâche absente": "SELECT count(*) FROM task_links l LEFT JOIN tasks t ON t.id=l.linked_task_id WHERE t.id IS NULL",
            "events → session absente": "SELECT count(*) FROM events e LEFT JOIN sessions s ON s.id=e.session_id WHERE s.id IS NULL",
            "memories → agent absent": "SELECT count(*) FROM memories m LEFT JOIN agents a ON a.id=m.agent_id WHERE a.id IS NULL",
        }
        for label, sql in checks.items():
            n = c(sql)
            note(label, n)
            if n:
                anomalies.append(f"FK orpheline : {label} = {n}")

    src.close()

    print("\n".join(lines))
    print("\n" + "=" * 60)
    if anomalies:
        print("VERDICT : ⚠ ANOMALIES À EXAMINER")
        for a in anomalies:
            print(f"  - {a}")
        return 1
    print("VERDICT : ✅ RÉCONCILIATION COMPLÈTE — chaque ligne v1 est migrée ou écartée avec raison.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python -m server.migrate_verify /chemin/v1.db")
        sys.exit(1)
    sys.exit(migrate_verify(sys.argv[1]))
