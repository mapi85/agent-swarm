"""Navigateur d'artefacts sur disque : espace de travail d'un agent
(data/agents/<id>/) et d'une tâche (data/tasks/<id>/). Lecture seule, avec
contrôle d'appartenance et garde anti-traversée de chemin."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Agent, Task, User
from ..security import ensure_owner, get_current_user

router = APIRouter(tags=["artifacts"])


def _list_files(base: Path) -> list[dict]:
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            st = p.stat()
            out.append({
                "path": p.relative_to(base).as_posix(),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="minutes"),
            })
    return out


def _safe(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    b = base.resolve()
    if target != b and b not in target.parents:
        raise HTTPException(status_code=400, detail="Chemin hors de l'espace")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    return target


async def _agent_visible(db, user, agent_id) -> Agent:
    a = await db.get(Agent, agent_id)
    if a is None or a.owner_user_id not in (None, user.id):
        raise HTTPException(status_code=404, detail="Agent introuvable")
    return a


async def _task_visible(db, user, task_id) -> Task:
    t = await db.get(Task, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable")
    ensure_owner(user, t.owner_user_id)
    return t


@router.get("/api/agents/{agent_id}/artifacts")
async def agent_artifacts(agent_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _agent_visible(db, user, agent_id)
    return {"files": _list_files(get_settings().agents_dir / str(agent_id))}


@router.get("/api/agents/{agent_id}/artifact")
async def agent_artifact(agent_id: int, path: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _agent_visible(db, user, agent_id)
    f = _safe(get_settings().agents_dir / str(agent_id), path)
    return FileResponse(f, filename=Path(path).name)


@router.get("/api/tasks/{task_id}/artifacts")
async def task_artifacts(task_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _task_visible(db, user, task_id)
    return {"files": _list_files(get_settings().tasks_dir / str(task_id))}


@router.get("/api/tasks/{task_id}/artifact")
async def task_artifact(task_id: int, path: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _task_visible(db, user, task_id)
    f = _safe(get_settings().tasks_dir / str(task_id), path)
    return FileResponse(f, filename=Path(path).name)
