"""Authentification : hashing argon2, tokens opaques révocables, dépendances FastAPI.

Les tokens d'API sont des chaînes aléatoires opaques ; seul leur SHA-256 est
stocké (table user_tokens). Révocation individuelle possible ; le changement de
mot de passe révoque tous les tokens de l'utilisateur.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import User, UserToken

TOKEN_TTL_DAYS = 30

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_token(db: AsyncSession, user: User) -> str:
    """Crée un token pour l'utilisateur et renvoie sa valeur en clair (montrée une seule fois)."""
    raw = secrets.token_urlsafe(32)
    db.add(
        UserToken(
            user_id=user.id,
            token_hash=_token_hash(raw),
            expires_at=datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS),
        )
    )
    return raw


async def revoke_token(db: AsyncSession, raw: str) -> None:
    await db.execute(delete(UserToken).where(UserToken.token_hash == _token_hash(raw)))


async def revoke_all_tokens(db: AsyncSession, user_id: int) -> None:
    await db.execute(delete(UserToken).where(UserToken.user_id == user_id))


async def _authenticate(raw: str, db: AsyncSession) -> User | None:
    result = await db.execute(
        select(User)
        .join(UserToken, UserToken.user_id == User.id)
        .where(
            UserToken.token_hash == _token_hash(raw),
            UserToken.expires_at > datetime.now(timezone.utc),
        )
    )
    return result.scalar_one_or_none()


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail="Non authentifié")
    user = await _authenticate(creds.credentials, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Compte inactif")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Réservé à l'administrateur")
    return user


def ensure_owner(user: User, owner_user_id: int | None) -> None:
    """Contrôle d'appartenance : l'admin voit tout ; un objet système
    (owner NULL) est lisible par tous — les routes d'écriture des objets
    système passent par require_admin en amont."""
    if user.role == "admin" or owner_user_id is None:
        return
    if owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Introuvable")
