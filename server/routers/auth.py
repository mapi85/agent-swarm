"""Inscription, connexion, session courante.

- L'inscription crée un compte `pending` (validation admin requise) et alerte
  les admins via une notification.
- Exception d'amorçage : le tout premier compte créé devient admin actif.
- Anti-force-brute : limitation en mémoire des tentatives de connexion
  (suffisant en mono-processus ; à revoir si multi-instances).
"""
import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Notification, User
from ..schemas import ChangePasswordIn, LoginIn, RegisterIn, TokenOut, UserOut
from ..security import (
    get_current_user,
    hash_password,
    issue_token,
    revoke_all_tokens,
    revoke_token,
    verify_password,
)

log = logging.getLogger("swarm.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

# --- limitation des tentatives : 10 échecs / 15 min par (ip, email) ---------
_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 900
_attempts: dict[tuple[str, str], list[float]] = defaultdict(list)


def _check_rate_limit(ip: str, email: str) -> None:
    now = time.monotonic()
    key = (ip, email.lower())
    _attempts[key] = [t for t in _attempts[key] if now - t < _WINDOW_SECONDS]
    if len(_attempts[key]) >= _MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Trop de tentatives, réessaie plus tard")


def _record_failure(ip: str, email: str) -> None:
    _attempts[(ip, email.lower())].append(time.monotonic())


async def _get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    return result.scalar_one_or_none()


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterIn, request: Request, db: AsyncSession = Depends(get_db)):
    _check_rate_limit(request.client.host if request.client else "?", body.email)
    if await _get_by_email(db, body.email) is not None:
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet email")

    first_user = (await db.execute(select(func.count()).select_from(User))).scalar_one() == 0
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role="admin" if first_user else "user",
        status="active" if first_user else "pending",
    )
    db.add(user)
    await db.flush()

    if not first_user:
        admins = (await db.execute(select(User).where(User.role == "admin", User.status == "active"))).scalars()
        for admin in admins:
            db.add(
                Notification(
                    user_id=admin.id,
                    type="alert",
                    content=f"Nouvelle inscription en attente de validation : "
                    f"**{user.display_name}** ({user.email})",
                )
            )
    await db.commit()
    log.info("inscription", extra={"user_id": user.id, "first_user": first_user})
    return user


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    _check_rate_limit(ip, body.email)
    user = await _get_by_email(db, body.email)
    if user is None or not verify_password(user.password_hash, body.password):
        _record_failure(ip, body.email)
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    if user.status == "pending":
        raise HTTPException(status_code=403, detail="Compte en attente de validation par l'administrateur")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Compte désactivé")
    token = await issue_token(db, user)
    await db.commit()
    return TokenOut(token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if creds is not None:
        await revoke_token(db, creds.credentials)
        await db.commit()


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect")
    user.password_hash = hash_password(body.new_password)
    await revoke_all_tokens(db, user.id)  # invalide toutes les sessions, y compris celle-ci
    await db.commit()
