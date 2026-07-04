"""Jauges de consommation de l'utilisateur courant (quotas)."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..quotas import user_quota_status
from ..schemas import UsageOut
from ..security import get_current_user

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("", response_model=UsageOut)
async def my_usage(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await user_quota_status(db, user)
