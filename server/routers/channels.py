"""Canaux de notification par utilisateur (email / Telegram) + config SMTP globale (admin).

Chaque utilisateur gère ses propres canaux ; les questions/alertes des tâches
qu'il possède y sont routées. Les secrets (bot_token, mot de passe SMTP) sont
chiffrés en base et jamais renvoyés en clair."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import notify
from ..config import get_settings
from ..crypto import encrypt_secret
from ..db import get_db
from ..models import AppSetting, NotificationChannel, User
from ..security import get_current_user, require_admin

router = APIRouter(tags=["channels"])


# --------------------------------------------------------------------------
# Schémas
# --------------------------------------------------------------------------

class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = Field(pattern="^(email|telegram)$")
    # email
    to: str | None = None
    # telegram (bot_token inchangé si None en édition)
    bot_token: str | None = None
    chat_id: str | None = None
    use_for_alerts: bool = True
    use_for_questions: bool = False
    enabled: bool = True


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    to: str | None = None
    bot_token: str | None = None
    chat_id: str | None = None
    use_for_alerts: bool | None = None
    use_for_questions: bool | None = None
    enabled: bool | None = None


class ChannelOut(BaseModel):
    id: int
    name: str
    type: str
    use_for_alerts: bool
    use_for_questions: bool
    enabled: bool
    # config masquée : on n'expose que la présence des secrets et le destinataire
    to: str | None = None
    chat_id: str | None = None
    bot_token_set: bool = False


def _to_out(ch: NotificationChannel) -> ChannelOut:
    cfg = notify.channel_config(ch)
    return ChannelOut(
        id=ch.id, name=ch.name, type=ch.type, use_for_alerts=ch.use_for_alerts,
        use_for_questions=ch.use_for_questions, enabled=ch.enabled,
        to=cfg.get("to"), chat_id=cfg.get("chat_id"), bot_token_set=bool(cfg.get("bot_token")),
    )


# --------------------------------------------------------------------------
# Canaux (par utilisateur)
# --------------------------------------------------------------------------

channels = APIRouter(prefix="/api/channels", tags=["channels"])


async def _get_own(db: AsyncSession, user: User, cid: int) -> NotificationChannel:
    ch = await db.get(NotificationChannel, cid)
    if ch is None or (user.role != "admin" and ch.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="Canal introuvable")
    return ch


@channels.get("", response_model=list[ChannelOut])
async def list_channels(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(NotificationChannel).where(NotificationChannel.owner_user_id == user.id)
            .order_by(NotificationChannel.id)
        )
    ).scalars().all()
    return [_to_out(ch) for ch in rows]


@channels.post("", response_model=ChannelOut, status_code=201)
async def create_channel(body: ChannelIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    cfg: dict = {}
    if body.type == "email":
        if not body.to:
            raise HTTPException(status_code=422, detail="Adresse email requise")
        cfg["to"] = body.to
    else:  # telegram
        if not body.bot_token or not body.chat_id:
            raise HTTPException(status_code=422, detail="bot_token et chat_id requis")
        cfg = {"bot_token": body.bot_token, "chat_id": body.chat_id,
               "secret_token": notify.new_secret_token()}
    ch = NotificationChannel(
        owner_user_id=user.id, name=body.name, type=body.type,
        config_enc=notify.encode_channel_config(cfg),
        use_for_alerts=body.use_for_alerts, use_for_questions=body.use_for_questions,
        enabled=body.enabled,
    )
    db.add(ch)
    await db.commit()
    await _maybe_register_webhook(ch, cfg)
    return _to_out(ch)


@channels.patch("/{cid}", response_model=ChannelOut)
async def patch_channel(cid: int, body: ChannelPatch, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ch = await _get_own(db, user, cid)
    cfg = notify.channel_config(ch)
    if body.to is not None:
        cfg["to"] = body.to
    if body.chat_id is not None:
        cfg["chat_id"] = body.chat_id
    if body.bot_token is not None and body.bot_token != "":
        cfg["bot_token"] = body.bot_token
    if ch.type == "telegram" and not cfg.get("secret_token"):
        cfg["secret_token"] = notify.new_secret_token()
    ch.config_enc = notify.encode_channel_config(cfg)
    for field in ("name", "use_for_alerts", "use_for_questions", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(ch, field, val)
    await db.commit()
    if body.bot_token or body.chat_id:
        await _maybe_register_webhook(ch, cfg)
    return _to_out(ch)


@channels.delete("/{cid}", status_code=204)
async def delete_channel(cid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ch = await _get_own(db, user, cid)
    await db.delete(ch)
    await db.commit()


@channels.post("/{cid}/test")
async def test_channel(cid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ch = await _get_own(db, user, cid)
    return {"result": await notify.send_test(ch, db)}


async def _maybe_register_webhook(ch: NotificationChannel, cfg: dict) -> None:
    base = get_settings().public_base_url
    if ch.type == "telegram" and base and cfg.get("bot_token") and cfg.get("secret_token"):
        await notify.set_telegram_webhook(cfg["bot_token"], ch.id, base, cfg["secret_token"])


# --------------------------------------------------------------------------
# SMTP global (admin)
# --------------------------------------------------------------------------

smtp_router = APIRouter(prefix="/api/settings/smtp", tags=["channels"], dependencies=[Depends(require_admin)])


class SmtpIn(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    password: str | None = None  # None = inchangé
    from_addr: str = ""


class SmtpOut(BaseModel):
    host: str
    port: int
    user: str
    from_addr: str
    password_set: bool


@smtp_router.get("", response_model=SmtpOut)
async def get_smtp(db: AsyncSession = Depends(get_db)):
    row = await db.get(AppSetting, "smtp_config")
    v = dict(row.value) if row and row.value else {}
    return SmtpOut(host=v.get("host", ""), port=int(v.get("port", 587)), user=v.get("user", ""),
                   from_addr=v.get("from_addr", ""), password_set=bool(v.get("password_enc")))


@smtp_router.put("", response_model=SmtpOut)
async def put_smtp(body: SmtpIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(AppSetting, "smtp_config")
    v = dict(row.value) if row and row.value else {}
    v.update(host=body.host, port=body.port, user=body.user, from_addr=body.from_addr)
    if body.password is not None:
        v["password_enc"] = encrypt_secret(body.password) if body.password else ""
    if row is None:
        db.add(AppSetting(key="smtp_config", value=v))
    else:
        row.value = v
    await db.commit()
    return SmtpOut(host=v.get("host", ""), port=int(v.get("port", 587)), user=v.get("user", ""),
                   from_addr=v.get("from_addr", ""), password_set=bool(v.get("password_enc")))
