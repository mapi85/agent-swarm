"""Gestion des comptes (admin) : validation des inscriptions, rôles, quotas.

Pas de suppression de compte : un utilisateur possède agents, missions et
tâches — on désactive (status=disabled), l'historique reste intègre.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..quotas import user_quota_status
from ..schemas import SetPasswordIn, UsageOut, UserCreateIn, UserOut, UserPatchIn
from ..security import hash_password, require_admin, revoke_all_tokens

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


async def _get_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    return user


@router.get("", response_model=list[UserOut])
async def list_users(status: str | None = None, db: AsyncSession = Depends(get_db)):
    query = select(User).order_by(User.id)
    if status:
        query = query.where(User.status == status)
    return (await db.execute(query)).scalars().all()


@router.post("", response_model=UserOut, status_code=201)
async def create_user(body: UserCreateIn, db: AsyncSession = Depends(get_db)):
    exists = (
        await db.execute(select(User.id).where(User.email.ilike(body.email)))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet email")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


@router.post("/{user_id}/approve", response_model=UserOut)
async def approve_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await _get_or_404(db, user_id)
    if user.status != "pending":
        raise HTTPException(status_code=400, detail="Ce compte n'est pas en attente")
    user.status = "active"
    await db.commit()
    return user


@router.post("/{user_id}/disable", response_model=UserOut)
async def disable_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de désactiver son propre compte")
    user = await _get_or_404(db, user_id)
    user.status = "disabled"
    await revoke_all_tokens(db, user.id)
    await db.commit()
    return user


@router.post("/{user_id}/enable", response_model=UserOut)
async def enable_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await _get_or_404(db, user_id)
    if user.status != "disabled":
        raise HTTPException(status_code=400, detail="Ce compte n'est pas désactivé")
    user.status = "active"
    await db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: int,
    body: UserPatchIn,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_or_404(db, user_id)
    fields = body.model_dump(exclude_unset=True)
    if user_id == admin.id and fields.get("role") == "user":
        raise HTTPException(status_code=400, detail="Impossible de retirer son propre rôle admin")
    for key, value in fields.items():
        setattr(user, key, value)
    await db.commit()
    return user


@router.get("/{user_id}/usage", response_model=UsageOut)
async def user_usage(user_id: int, db: AsyncSession = Depends(get_db)):
    return await user_quota_status(db, await _get_or_404(db, user_id))


@router.post("/{user_id}/set-password", status_code=204)
async def set_password(user_id: int, body: SetPasswordIn, db: AsyncSession = Depends(get_db)):
    user = await _get_or_404(db, user_id)
    user.password_hash = hash_password(body.new_password)
    await revoke_all_tokens(db, user.id)
    await db.commit()
