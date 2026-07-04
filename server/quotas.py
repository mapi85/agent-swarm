"""Quotas de tokens par utilisateur et jauges de consommation (table token_usage).

Fenêtres glissantes : X tokens / Y heures (court terme) et Z tokens / W jours
(long terme), 0 = illimité. Mêmes fenêtres pour les limites de providers.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TokenUsage, User


async def _window_total(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    provider_id: int | None = None,
    since: datetime,
) -> int:
    query = select(func.coalesce(func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens), 0)).where(
        TokenUsage.ts >= since
    )
    if user_id is not None:
        query = query.where(TokenUsage.user_id == user_id)
    if provider_id is not None:
        query = query.where(TokenUsage.provider_id == provider_id)
    return (await db.execute(query)).scalar_one()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def user_quota_status(db: AsyncSession, user: User) -> dict:
    """Jauges de l'utilisateur : consommé/plafond par fenêtre (0 = illimité)."""
    short_used = long_used = 0
    if user.quota_short_hours:
        short_used = await _window_total(db, user_id=user.id, since=_now() - timedelta(hours=user.quota_short_hours))
    if user.quota_long_days:
        long_used = await _window_total(db, user_id=user.id, since=_now() - timedelta(days=user.quota_long_days))
    return {
        "short_used": short_used,
        "short_limit": user.quota_short_tokens,
        "short_hours": user.quota_short_hours,
        "long_used": long_used,
        "long_limit": user.quota_long_tokens,
        "long_days": user.quota_long_days,
    }


async def user_quota_exceeded(db: AsyncSession, user: User) -> str | None:
    """None si l'utilisateur peut consommer, sinon le motif du refus.
    Appelé par le scheduler/runtime avant et pendant les sessions."""
    status = await user_quota_status(db, user)
    if status["short_limit"] and status["short_hours"] and status["short_used"] >= status["short_limit"]:
        return (
            f"Quota court terme atteint ({status['short_used']}/{status['short_limit']} tokens "
            f"sur {status['short_hours']} h)"
        )
    if status["long_limit"] and status["long_days"] and status["long_used"] >= status["long_limit"]:
        return (
            f"Quota long terme atteint ({status['long_used']}/{status['long_limit']} tokens "
            f"sur {status['long_days']} j)"
        )
    return None


async def provider_usage(db: AsyncSession, provider_id: int, hours: int, days: int) -> dict:
    """Consommation d'un provider sur ses fenêtres de limite (pour les jauges admin)."""
    short_used = long_used = 0
    if hours:
        short_used = await _window_total(db, provider_id=provider_id, since=_now() - timedelta(hours=hours))
    if days:
        long_used = await _window_total(db, provider_id=provider_id, since=_now() - timedelta(days=days))
    return {"short_used": short_used, "long_used": long_used}


def record_usage(
    db: AsyncSession,
    *,
    user_id: int | None,
    provider_id: int | None,
    agent_id: int | None = None,
    task_id: int | None = None,
    session_id: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Une ligne par appel LLM (le commit appartient à l'appelant)."""
    db.add(
        TokenUsage(
            user_id=user_id,
            provider_id=provider_id,
            agent_id=agent_id,
            task_id=task_id,
            session_id=session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )
