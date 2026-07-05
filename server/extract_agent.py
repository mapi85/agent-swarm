"""Dossier complet d'un agent (mémoire, bilans, tâches, ressources, artefacts)
pour préparer sa reconstruction curée. Lecture seule.

    python -m server.extract_agent <agent_id>
"""
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from .config import get_settings


def _tree(base: Path, limit=40):
    if not base.is_dir():
        return 0, 0, []
    files = [p for p in base.rglob("*") if p.is_file()]
    total_size = sum(p.stat().st_size for p in files)
    sample = [f"{p.relative_to(base).as_posix()} ({p.stat().st_size}o)" for p in files[:limit]]
    return len(files), total_size, sample


def extract(agent_id: int) -> None:
    s = get_settings()
    engine = create_engine(s.database_url)
    with engine.connect() as db:
        def q(sql, **p):
            return db.execute(text(sql), p).fetchall()

        a = q("SELECT * FROM agents WHERE id=:i", i=agent_id)
        if not a:
            print(f"agent #{agent_id} introuvable"); return
        a = a[0]
        owner = q("SELECT display_name FROM users WHERE id=:i", i=a.owner_user_id)
        owner = owner[0][0] if owner else "système"

        print("#" * 78)
        print(f"# AGENT #{a.id} {a.name}  [{a.category}]  modèle={a.model}  effort={a.effort}")
        print(f"# propriétaire: {owner}  |  provider_id={a.provider_id}  budget={a.session_token_budget}")
        print("#" * 78)
        print(f"\n## MISSION (prompt système)\n{a.mission_prompt}")

        print("\n## MÉMOIRE STRUCTURÉE (clé = valeur)")
        for m in q("SELECT scope,task_id,mkey,mvalue FROM memories WHERE agent_id=:i ORDER BY scope,mkey", i=agent_id):
            tag = f"[tâche {m.task_id}]" if m.scope == "task" else "[agent]"
            print(f"  {tag} {m.mkey} = {(m.mvalue or '')[:300]}")

        # MEMORY.md sur disque (par utilisateur)
        base = s.agents_dir / str(agent_id)
        for md in (base / "memory").rglob("MEMORY.md"):
            print(f"\n## MEMORY.md ({md.relative_to(base)})\n{md.read_text(encoding='utf-8', errors='replace')[:4000]}")

        print("\n## TÂCHES (toutes)")
        for t in q("SELECT id,title,status,description,result,mission_id FROM tasks WHERE agent_id=:i ORDER BY id", i=agent_id):
            print(f"  #{t.id} [{t.status}] {t.title or ''}"
                  f"{' (mission '+str(t.mission_id)+')' if t.mission_id else ''}")
            print(f"      desc: {(t.description or '')[:200]}")
            if t.result:
                print(f"      résultat: {t.result[:400]}")

        print("\n## RESSOURCES liées aux tâches de l'agent")
        rs = q("""SELECT r.id,r.scope,r.task_id,r.name,r.kind,r.description
                  FROM resources r JOIN tasks t ON t.id=r.task_id WHERE t.agent_id=:i ORDER BY r.id""", i=agent_id)
        for r in rs:
            print(f"  #{r.id} [{r.scope} tâche {r.task_id}] {r.name} ({r.kind}) — {(r.description or '')[:120]}")
        if not rs:
            print("  (aucune)")

        print("\n## BILANS DE SESSION (12 derniers complétés)")
        for se in q("""SELECT id,number,ended_at,report FROM sessions
                       WHERE agent_id=:i AND status='completed' AND report IS NOT NULL
                       ORDER BY id DESC LIMIT 12""", i=agent_id):
            print(f"  --- session #{se.id} (n°{se.number}, {se.ended_at}) ---")
            print(f"  {(se.report or '')[:600]}")

        print("\n## ARTEFACTS SUR DISQUE")
        for label, sub in (("library", "library"), ("livrables v1", "legacy_deliverables")):
            n, size, sample = _tree(base / sub)
            print(f"  [{label}] {n} fichier(s), {size} o")
            for f in sample:
                print(f"      {f}")
            if n > len(sample):
                print(f"      … (+{n - len(sample)} autres)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python -m server.extract_agent <agent_id>"); sys.exit(1)
    extract(int(sys.argv[1]))
