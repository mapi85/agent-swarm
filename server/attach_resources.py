"""Rerattachement des ressources v1 (uploadées) au modèle v2, aux emplacements
appropriés. Les fichiers sont déjà sur disque (data/resources/) ; on recrée les
lignes en base. Métadonnées lues dans le dump v1 (/tmp/v1.db).

Exclut volontairement les artefacts offensifs (leurres de phishing de craftsman)
et ceux des agents laissés en archive (avatar-3d).

    docker compose ... run --rm -v ~/mig/v1.db:/tmp/v1.db:ro app python -m server.attach_resources --apply
"""
import sqlite3
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from .config import get_settings

V1DB = "/tmp/v1.db"
OWNER = 1  # admin (Par défaut)

SHARED_NOTES = [1, 2, 3, 13]          # notes mutualisées (contenu en base v1)
OVERMIND_FILES = [4, 5, 7, 10, 11, 14, 15, 16, 17, 18, 19]  # dossier de preuve → user
LEDGER_FILE = 6                        # bilan fraude crypto → tâche-socle de ledger (#12)
# Exclus : 8,9,12 (craftsman, leurres phishing) ; 20,21 (avatar-3d, en archive)


def attach(apply: bool) -> None:
    s = get_settings()
    engine = create_engine(s.database_url)
    v1 = sqlite3.connect(V1DB)
    v1.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    res = {r["id"]: r for r in v1.execute("SELECT * FROM resources")}

    def disk_ok(fn):
        return fn and (s.resources_dir / fn).exists()

    plan = []
    for rid in SHARED_NOTES:
        r = res.get(rid)
        if r:
            plan.append(("shared", None, None, r))
    for rid in OVERMIND_FILES:
        r = res.get(rid)
        if r and disk_ok(r["filename"]):
            plan.append(("user", OWNER, None, r))
    r6 = res.get(LEDGER_FILE)

    print(f"=== ASSOCIATION RESSOURCES — {'APPLIQUÉ' if apply else 'DRY-RUN'} ===")
    print(f"Notes mutualisées : {SHARED_NOTES} · dossier preuve (user) : {len(OVERMIND_FILES)} fichiers "
          f"· bilan crypto → ledger : {LEDGER_FILE}")
    print("Exclus (archive) : 8,9,12 (leurres phishing craftsman) ; 20,21 (avatar-3d)")
    if not apply:
        return

    with engine.begin() as db:
        # tâche-socle de ledger (#12)
        ledger_socle = db.execute(text(
            "SELECT id FROM tasks WHERE agent_id=12 AND status='done' AND title LIKE 'Socle%' "
            "ORDER BY id LIMIT 1")).scalar()

        def insert(scope, owner, task_id, r, filename):
            db.execute(text(
                "INSERT INTO resources (scope,owner_user_id,task_id,name,kind,filename,content,description,"
                "size,created_by,created_at) VALUES (:sc,:o,:tk,:n,:k,:fn,:c,:d,:sz,'migration',:t)"),
                {"sc": scope, "o": owner, "tk": task_id, "n": r["name"], "k": r["kind"],
                 "fn": filename, "c": r["content"],
                 "d": r["description"] or "Ressource héritée (dossier de preuve)" if scope != "shared" else (r["description"] or ""),
                 "sz": (s.resources_dir / filename).stat().st_size if filename and (s.resources_dir / filename).exists() else (len(r["content"] or "")),
                 "t": now})

        n = 0
        for scope, owner, task_id, r in plan:
            insert(scope, owner, task_id, r, r["filename"] if r["kind"] == "file" else None)
            n += 1
        if r6 and disk_ok(r6["filename"]) and ledger_socle:
            insert("task", OWNER, ledger_socle, r6, r6["filename"])
            n += 1
        print(f"  ressources recréées : {n}")

    v1.close()
    print("\n✅ Ressources associées.")


if __name__ == "__main__":
    attach("--apply" in sys.argv)
