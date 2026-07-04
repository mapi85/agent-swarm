"""Migration des FICHIERS v1 → v2 (workdirs des agents + fichiers de ressources).

À lancer APRÈS la migration de la base (migrate_v1), car il lit le mapping
agent→propriétaire depuis la base v2.

Usage (conteneur v2, avec le volume v1 monté en lecture seule sur /v1) :
    python -m server.migrate_files /v1

Transformations :
- data/agents/<id>_<nom>/library/      → data/agents/<id>/library/
- data/agents/<id>_<nom>/memory/*      → data/agents/<id>/memory/users/<owner_uid>/
  (scission par utilisateur ; agents système → utilisateur admin)
- data/agents/<id>_<nom>/deliverables/ → data/agents/<id>/legacy_deliverables/
  (préservés ; en v2 les livrables sont par tâche, pas d'attribution possible a posteriori)
- data/resources/*                     → data/resources/  (ids préservés, copie directe)

Idempotent : réécrit les destinations (copie). N'efface jamais la source.
Seuls les agents présents dans la base v2 sont traités (les workdirs d'agents
supprimés en v1 sont ignorés).
"""
import shutil
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from .config import get_settings


def _copy_tree(src: Path, dst: Path) -> int:
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            n += 1
    return n


def migrate_files(v1_root: str) -> None:
    v1 = Path(v1_root)
    settings = get_settings()
    v2_agents = settings.agents_dir
    v2_resources = settings.resources_dir
    v2_agents.mkdir(parents=True, exist_ok=True)
    v2_resources.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url)
    with engine.connect() as db:
        admin_uid = db.execute(
            text("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        ).scalar()
        agents = db.execute(text("SELECT id, owner_user_id FROM agents")).fetchall()

    if admin_uid is None:
        print("ABANDON : aucun utilisateur admin en base (lance d'abord migrate_v1).")
        sys.exit(1)

    v1_agents_dir = v1 / "agents"
    report = {"agents": 0, "library_files": 0, "memory_files": 0, "deliverable_files": 0,
              "resource_files": 0, "sans_workdir": []}

    for agent_id, owner in agents:
        owner_uid = owner if owner is not None else admin_uid
        # Trouve le dossier v1 <id>_<nom> (préfixe = id exact)
        matches = [d for d in v1_agents_dir.glob(f"{agent_id}_*") if d.is_dir()]
        if not matches:
            report["sans_workdir"].append(agent_id)
            continue
        src = matches[0]
        dst = v2_agents / str(agent_id)
        report["library_files"] += _copy_tree(src / "library", dst / "library")
        report["memory_files"] += _copy_tree(src / "memory", dst / "memory" / "users" / str(owner_uid))
        report["deliverable_files"] += _copy_tree(src / "deliverables", dst / "legacy_deliverables")
        report["agents"] += 1

    # Fichiers de ressources (ids préservés → copie directe)
    v1_res = v1 / "resources"
    if v1_res.is_dir():
        report["resource_files"] = _copy_tree(v1_res, v2_resources)

    print("\n=== MIGRATION DES FICHIERS TERMINÉE ===")
    for k, v in report.items():
        print(f"  {k:18} : {v}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python -m server.migrate_files /chemin/volume/v1")
        sys.exit(1)
    migrate_files(sys.argv[1])
