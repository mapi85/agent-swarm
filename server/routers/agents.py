"""Agents : dédiés (propriété d'un utilisateur) ou système (owner NULL,
gérés par l'admin, utilisables par tous). L'état d'exécution se dérive des
tâches/sessions ; ici on ne gère que le paramétrage et le cycle de vie."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Agent, Provider, Session, Task, User
from ..schemas import AgentCreateIn, AgentOut, AgentPatchIn
from ..security import get_current_user, require_admin

router = APIRouter(prefix="/api/agents", tags=["agents"])

OPEN_STATUSES = ("pending", "ready", "waiting_user")


async def _get_visible(db: AsyncSession, user: User, agent_id: int) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent introuvable")
    if user.role != "admin" and agent.owner_user_id not in (None, user.id):
        raise HTTPException(status_code=404, detail="Agent introuvable")
    return agent


def _require_manage(user: User, agent: Agent) -> None:
    """Modifier/mettre en pause : le propriétaire, ou l'admin (seul habilité
    pour les agents système)."""
    if user.role == "admin":
        return
    if agent.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Cet agent est géré par l'administrateur")


async def _to_out(db: AsyncSession, agent: Agent) -> AgentOut:
    out = AgentOut.model_validate(agent)
    counts = dict(
        (
            await db.execute(
                select(Task.status, func.count())
                .where(Task.agent_id == agent.id, Task.status.in_((*OPEN_STATUSES, "in_progress")))
                .group_by(Task.status)
            )
        ).all()
    )
    out.running_tasks = counts.pop("in_progress", 0)
    out.open_tasks = sum(counts.values())
    out.next_session_at = (
        await db.execute(
            select(func.min(Session.scheduled_at))
            .where(Session.agent_id == agent.id, Session.status == "planned")
        )
    ).scalar()
    return out


async def _validate_provider_model(db: AsyncSession, provider_id: int | None, model: str) -> str:
    if provider_id is not None:
        provider = await db.get(Provider, provider_id)
        if provider is None:
            raise HTTPException(status_code=422, detail="Provider inconnu")
        if not model:
            model = provider.default_model
    if not model:
        default = (
            await db.execute(select(Provider).where(Provider.is_default.is_(True)))
        ).scalar_one_or_none()
        model = default.default_model if default else ""
    if not model:
        raise HTTPException(status_code=422, detail="Préciser un modèle (aucun modèle par défaut)")
    return model


@router.get("", response_model=list[AgentOut])
async def list_agents(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(Agent).order_by(Agent.name)
    if user.role != "admin":
        query = query.where((Agent.owner_user_id == user.id) | (Agent.owner_user_id.is_(None)))
    agents = (await db.execute(query)).scalars().all()
    return [await _to_out(db, a) for a in agents]


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _to_out(db, await _get_visible(db, user, agent_id))


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    body: AgentCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if body.system and user.role != "admin":
        raise HTTPException(status_code=403, detail="Seul l'admin crée des agents système")
    model = await _validate_provider_model(db, body.provider_id, body.model)
    agent = Agent(
        **body.model_dump(exclude={"system", "model"}),
        model=model,
        owner_user_id=None if body.system else user.id,
    )
    db.add(agent)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Un agent porte déjà ce nom dans ce périmètre")
    (get_settings().agents_dir / str(agent.id)).mkdir(parents=True, exist_ok=True)
    return await _to_out(db, agent)


@router.patch("/{agent_id}", response_model=AgentOut)
async def patch_agent(
    agent_id: int,
    body: AgentPatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _get_visible(db, user, agent_id)
    _require_manage(user, agent)
    fields = body.model_dump(exclude_unset=True)
    if "provider_id" in fields or "model" in fields:
        provider_id = fields.get("provider_id", agent.provider_id)
        model = fields.get("model") or ("" if "provider_id" in fields else agent.model)
        fields["model"] = await _validate_provider_model(db, provider_id, model)
    for key, value in fields.items():
        setattr(agent, key, value)
    await db.commit()
    return await _to_out(db, agent)


@router.post("/{agent_id}/pause", response_model=AgentOut)
async def pause_agent(
    agent_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    agent = await _get_visible(db, user, agent_id)
    _require_manage(user, agent)
    agent.paused = True
    await db.commit()
    return await _to_out(db, agent)


@router.post("/{agent_id}/resume", response_model=AgentOut)
async def resume_agent(
    agent_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    agent = await _get_visible(db, user, agent_id)
    _require_manage(user, agent)
    agent.paused = False
    await db.commit()
    return await _to_out(db, agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Suppression réservée aux agents sans historique : dès qu'un agent a des
    tâches, on le met en pause au lieu de le supprimer (l'historique des
    missions doit rester intègre)."""
    agent = await _get_visible(db, user, agent_id)
    _require_manage(user, agent)
    has_tasks = (
        await db.execute(select(func.count()).select_from(Task).where(Task.agent_id == agent_id))
    ).scalar_one()
    if has_tasks:
        raise HTTPException(
            status_code=409,
            detail="Cet agent a un historique de tâches : le mettre en pause plutôt que le supprimer",
        )
    await db.delete(agent)
    await db.commit()
