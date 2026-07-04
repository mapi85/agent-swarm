"""Providers LLM mutualisés : lecture pour tous les utilisateurs actifs,
gestion (CRUD, ordre de secours, limites) réservée à l'admin.
Les clés API sont chiffrées en base et jamais renvoyées."""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_secret, encrypt_secret
from ..db import get_db
from ..llm import fetch_models
from ..models import Agent, Provider, User
from ..quotas import provider_usage
from ..schemas import FetchModelsIn, ProviderCreateIn, ProviderOut, ProviderPatchIn
from ..security import get_current_user, require_admin

router = APIRouter(prefix="/api/providers", tags=["providers"])


async def _get_or_404(db: AsyncSession, provider_id: int) -> Provider:
    provider = await db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider introuvable")
    return provider


async def _to_out(db: AsyncSession, provider: Provider) -> ProviderOut:
    out = ProviderOut.model_validate(provider)
    out.api_key_set = bool(provider.api_key_enc)
    usage = await provider_usage(db, provider.id, provider.limit_short_hours, provider.limit_long_days)
    out.usage_short = usage["short_used"]
    out.usage_long = usage["long_used"]
    out.agent_count = (
        await db.execute(select(func.count()).select_from(Agent).where(Agent.provider_id == provider.id))
    ).scalar_one()
    return out


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    providers = (await db.execute(select(Provider).order_by(Provider.priority, Provider.id))).scalars().all()
    return [await _to_out(db, p) for p in providers]


@router.post("", response_model=ProviderOut, status_code=201, dependencies=[Depends(require_admin)])
async def create_provider(body: ProviderCreateIn, db: AsyncSession = Depends(get_db)):
    exists = (await db.execute(select(Provider.id).where(Provider.name == body.name))).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="Un provider porte déjà ce nom")
    max_priority = (await db.execute(select(func.coalesce(func.max(Provider.priority), 0)))).scalar_one()
    provider = Provider(
        **body.model_dump(exclude={"api_key", "is_default"}),
        api_key_enc=encrypt_secret(body.api_key) if body.api_key else "",
        priority=max_priority + 1,
    )
    db.add(provider)
    await db.flush()
    if body.is_default:
        await _set_default(db, provider)
    await db.commit()
    return await _to_out(db, provider)


@router.patch("/{provider_id}", response_model=ProviderOut, dependencies=[Depends(require_admin)])
async def patch_provider(provider_id: int, body: ProviderPatchIn, db: AsyncSession = Depends(get_db)):
    provider = await _get_or_404(db, provider_id)
    fields = body.model_dump(exclude_unset=True)
    api_key = fields.pop("api_key", None)
    if api_key is not None:
        provider.api_key_enc = encrypt_secret(api_key) if api_key else ""
    for key, value in fields.items():
        setattr(provider, key, value)
    await db.commit()
    return await _to_out(db, provider)


@router.delete("/{provider_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    provider = await _get_or_404(db, provider_id)
    if provider.is_default:
        raise HTTPException(status_code=400, detail="Définir d'abord un autre provider par défaut")
    # Les agents rattachés basculent sur le provider par défaut (provider_id NULL)
    await db.execute(update(Agent).where(Agent.provider_id == provider_id).values(provider_id=None))
    await db.delete(provider)
    await db.commit()


async def _set_default(db: AsyncSession, provider: Provider) -> None:
    await db.execute(update(Provider).values(is_default=False))
    provider.is_default = True


@router.post("/{provider_id}/default", response_model=ProviderOut, dependencies=[Depends(require_admin)])
async def set_default(provider_id: int, db: AsyncSession = Depends(get_db)):
    provider = await _get_or_404(db, provider_id)
    await _set_default(db, provider)
    await db.commit()
    return await _to_out(db, provider)


@router.put("/order", status_code=204, dependencies=[Depends(require_admin)])
async def set_order(ids: list[int], db: AsyncSession = Depends(get_db)):
    """Ordre de la chaîne de secours : la liste complète des ids, premier essayé en tête."""
    providers = (await db.execute(select(Provider))).scalars().all()
    if sorted(ids) != sorted(p.id for p in providers):
        raise HTTPException(status_code=400, detail="La liste doit contenir exactement tous les providers")
    by_id = {p.id: p for p in providers}
    for position, provider_id in enumerate(ids, start=1):
        by_id[provider_id].priority = position
    await db.commit()


@router.post("/fetch-models", dependencies=[Depends(require_admin)])
async def fetch_models_endpoint(body: FetchModelsIn, db: AsyncSession = Depends(get_db)):
    """Récupère la liste des modèles depuis l'API du provider (existant ou en cours de saisie)."""
    ptype, base_url, api_key = body.ptype, body.base_url, body.api_key
    if body.provider_id is not None:
        provider = await _get_or_404(db, body.provider_id)
        ptype, base_url = provider.ptype, provider.base_url
        if not api_key:
            api_key = decrypt_secret(provider.api_key_enc) if provider.api_key_enc else ""
    try:
        models = await fetch_models(ptype, base_url, api_key)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Échec de l'interrogation du provider : {exc}")
    return {"models": models}
