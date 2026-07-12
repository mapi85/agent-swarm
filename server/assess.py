"""État des lieux par profil et par agent (lecture seule) — préparation de la
reconstruction curée. À lancer dans le conteneur v2 (accès base + volume).

    python -m server.assess
"""
from pathlib import Path

from sqlalchemy import create_engine, text

from .config import get_settings

OPEN = ("pending", "ready", "in_progress", "waiting_user", "stalled")


def _files(d: Path) -> int:
    return sum(1 for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def assess() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    agents_dir = settings.agents_dir

    with engine.connect() as db:
        def q(sql, **p):
            return db.execute(text(sql), p).fetchall()

        users = {u.id: (u.display_name, u.role) for u in q("SELECT id,display_name,role FROM users ORDER BY id")}
        agents = q("SELECT id,name,owner_user_id,category,model FROM agents ORDER BY owner_user_id NULLS LAST, id")

        # index par agent
        def stat(agent_id):
            done = db.execute(text("SELECT count(*) FROM tasks WHERE agent_id=:a AND status='done'"), {"a": agent_id}).scalar()
            openc = db.execute(text("SELECT count(*) FROM tasks WHERE agent_id=:a AND status = ANY(:s)"),
                               {"a": agent_id, "s": list(OPEN)}).scalar()
            failed = db.execute(text("SELECT count(*) FROM tasks WHERE agent_id=:a AND status IN ('failed','cancelled')"), {"a": agent_id}).scalar()
            res = db.execute(text("SELECT count(*) FROM resources r JOIN tasks t ON t.id=r.task_id WHERE t.agent_id=:a"), {"a": agent_id}).scalar()
            mem_a = db.execute(text("SELECT count(*) FROM memories WHERE agent_id=:a AND scope='agent'"), {"a": agent_id}).scalar()
            mem_t = db.execute(text("SELECT count(*) FROM memories WHERE agent_id=:a AND scope='task'"), {"a": agent_id}).scalar()
            sess = db.execute(text("SELECT count(*) FROM sessions WHERE agent_id=:a"), {"a": agent_id}).scalar()
            sess_rep = db.execute(text("SELECT count(*) FROM sessions WHERE agent_id=:a AND report IS NOT NULL AND status='completed'"), {"a": agent_id}).scalar()
            base = agents_dir / str(agent_id)
            return dict(done=done, openc=openc, failed=failed, res=res, mem_a=mem_a, mem_t=mem_t,
                        sess=sess, sess_rep=sess_rep,
                        f_lib=_files(base / "library"), f_deliv=_files(base / "legacy_deliverables"),
                        f_mem=_files(base / "memory"))

        # regroupe par propriétaire
        by_owner = {}
        for a in agents:
            by_owner.setdefault(a.owner_user_id, []).append(a)

        print("=" * 78)
        print("ÉTAT DES LIEUX — PAR PROFIL ET PAR AGENT")
        print("=" * 78)

        for owner_id, alist in by_owner.items():
            if owner_id is None:
                print(f"\n### AGENTS SYSTÈME (partagés, non dupliqués) — {len(alist)} agent(s)")
            else:
                name, role = users.get(owner_id, ("?", "?"))
                print(f"\n### PROFIL: {name} [{role}] (user {owner_id}) — {len(alist)} agent(s) dédié(s)")
            for a in alist:
                s = stat(a.id)
                print(f"  • #{a.id:2} {a.name:22} [{a.category or '-'}] {a.model}")
                print(f"      tâches: {s['done']} faites | {s['openc']} ouvertes | {s['failed']} échec/annul")
                print(f"      ressources(tâches): {s['res']} | mémoire: {s['mem_a']} agent + {s['mem_t']} tâche | "
                      f"sessions: {s['sess']} ({s['sess_rep']} avec bilan)")
                print(f"      fichiers: library {s['f_lib']} | livrables_v1 {s['f_deliv']} | mémoire {s['f_mem']}")

        # croisements profil : tâches d'un profil sur l'agent d'un AUTRE profil (non système)
        print("\n" + "=" * 78)
        print("CROISEMENTS DE PROFIL (agent d'un profil utilisé par un autre → à DUPLIQUER)")
        print("=" * 78)
        rows = q("""
            SELECT t.owner_user_id AS task_owner, a.id AS agent_id, a.name AS agent_name,
                   a.owner_user_id AS agent_owner, count(*) AS n,
                   count(*) FILTER (WHERE t.status = ANY(:s)) AS n_open
            FROM tasks t JOIN agents a ON a.id = t.agent_id
            WHERE a.owner_user_id IS NOT NULL AND t.owner_user_id <> a.owner_user_id
            GROUP BY 1,2,3,4 ORDER BY 1,2
        """, s=list(OPEN))
        if not rows:
            print("  (aucun — chaque agent n'est utilisé que par son propre profil)")
        for r in rows:
            to = users.get(r.task_owner, ("?",))[0]
            ao = users.get(r.agent_owner, ("?",))[0]
            print(f"  ⚠ profil «{to}» utilise l'agent #{r.agent_id} {r.agent_name} (de «{ao}») : "
                  f"{r.n} tâche(s) dont {r.n_open} ouverte(s) → dupliquer pour «{to}»")

        # synthèse globale
        print("\n" + "=" * 78)
        tot_open = db.execute(text("SELECT count(*) FROM tasks WHERE status = ANY(:s)"), {"s": list(OPEN)}).scalar()
        tot_done = db.execute(text("SELECT count(*) FROM tasks WHERE status='done'")).scalar()
        tot_fail = db.execute(text("SELECT count(*) FROM tasks WHERE status IN ('failed','cancelled')")).scalar()
        print(f"TOTAL tâches : {tot_open} ouvertes | {tot_done} faites | {tot_fail} échec/annul")
        print(f"TOTAL agents : {len(agents)} ({sum(1 for a in agents if a.owner_user_id is None)} système)")


if __name__ == "__main__":
    assess()
