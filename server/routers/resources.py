"""Ressources : consultées dans la tâche (scope task) ou le profil (scope user),
mutualisées (shared, admin). Remplace l'ancien Explorateur — l'accès se fait
depuis la tâche concernée."""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Resource, Task, User
from ..security import ensure_owner, get_current_user

router = APIRouter(prefix="/api/resources", tags=["resources"])

MAX_UPLOAD = 100 * 1024 * 1024


class ResourceOut(BaseModel):
    id: int
    scope: str
    owner_user_id: int | None
    task_id: int | None
    name: str
    kind: str
    content: str | None
    description: str
    size: int
    created_by: str

    class Config:
        from_attributes = True


class LinkIn(BaseModel):
    name: str
    url: str
    scope: str = "user"
    task_id: int | None = None
    description: str = ""


def _visible(query, user: User):
    # Ressources partagées (scope=shared) + ses propres ressources (admin compris).
    return query.where(or_(Resource.scope == "shared", Resource.owner_user_id == user.id))


@router.get("", response_model=list[ResourceOut])
async def list_resources(
    scope: str | None = None,
    task_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = _visible(select(Resource), user).order_by(Resource.id.desc())
    if scope:
        query = query.where(Resource.scope == scope)
    if task_id is not None:
        query = query.where(Resource.task_id == task_id)
    return (await db.execute(query)).scalars().all()


async def _get_visible(db: AsyncSession, user: User, rid: int) -> Resource:
    r = await db.get(Resource, rid)
    if r is None:
        raise HTTPException(status_code=404, detail="Ressource introuvable")
    if r.scope != "shared" and r.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Ressource introuvable")
    return r


@router.get("/{rid}/content")
async def resource_content(rid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    r = await _get_visible(db, user, rid)
    if r.kind == "file" and r.filename:
        fp = get_settings().resources_dir / r.filename
        if not fp.exists():
            raise HTTPException(status_code=404, detail="Fichier manquant")
        # FileResponse gère l'encodage RFC 5987 des noms non-ASCII (ex. tiret cadratin)
        return FileResponse(fp, filename=r.name)
    return PlainTextResponse(r.content or "")


@router.post("/link", response_model=ResourceOut, status_code=201)
async def create_link(body: LinkIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if body.task_id is not None:
        task = await db.get(Task, body.task_id)
        ensure_owner(user, task.owner_user_id if task else None)
    r = Resource(scope=body.scope, owner_user_id=user.id, task_id=body.task_id, name=body.name,
                 kind="link", content=body.url, description=body.description, size=len(body.url),
                 created_by="user")
    db.add(r)
    await db.commit()
    return r


@router.post("/upload", response_model=ResourceOut, status_code=201)
async def upload(
    file: UploadFile = File(...),
    scope: str = Form("user"),
    task_id: int | None = Form(None),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 100 Mo)")
    if task_id is not None:
        task = await db.get(Task, task_id)
        ensure_owner(user, task.owner_user_id if task else None)
    safe = Path(file.filename or "fichier").name
    r = Resource(scope=scope, owner_user_id=user.id, task_id=task_id, name=safe, kind="file",
                 description=description, size=len(data), created_by="user")
    db.add(r)
    await db.flush()
    settings = get_settings()
    settings.resources_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{r.id}_{safe}"
    (settings.resources_dir / stored).write_bytes(data)
    r.filename = stored
    await db.commit()
    return r


@router.delete("/{rid}", status_code=204)
async def delete_resource(rid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    r = await _get_visible(db, user, rid)
    if r.scope != "shared" and r.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Suppression réservée au propriétaire")
    if r.kind == "file" and r.filename:
        fp = get_settings().resources_dir / r.filename
        fp.unlink(missing_ok=True)
    await db.delete(r)
    await db.commit()
