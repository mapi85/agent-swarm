"""Webhook Telegram — public mais protégé par secret_token.

Telegram envoie l'en-tête `X-Telegram-Bot-Api-Secret-Token` défini lors de
setWebhook. On le compare (temps constant) au secret du canal. Une réponse
(reply) à une question ouverte y répond et relance la tâche."""
import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import func, select

from .. import notify
from ..db import SessionLocal
from ..models import NotificationChannel, Notification, Session, Task

log = logging.getLogger("swarm.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/telegram/{channel_id}")
async def telegram_webhook(
    channel_id: int,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    async with SessionLocal() as db:
        channel = await db.get(NotificationChannel, channel_id)
        if channel is None or channel.type != "telegram":
            raise HTTPException(status_code=404, detail="Canal introuvable")
        cfg = notify.channel_config(channel)
        secret = cfg.get("secret_token") or ""
        if not secret or not x_telegram_bot_api_secret_token \
                or not hmac.compare_digest(secret, x_telegram_bot_api_secret_token):
            raise HTTPException(status_code=403, detail="Secret invalide")

        update = await request.json()
        message = update.get("message") or {}
        reply_to = message.get("reply_to_message") or {}
        text = (message.get("text") or "").strip()
        replied_mid = reply_to.get("message_id")
        if not text or replied_mid is None:
            return {"ok": True}  # rien à traiter (message non-réponse)

        # Retrouver la question à laquelle ce reply répond (external_ids t_<channel_id>)
        key = f"t_{channel_id}"
        candidates = (
            await db.execute(
                select(Notification).where(
                    Notification.user_id == channel.owner_user_id,
                    Notification.type == "question",
                    Notification.status == "open",
                )
            )
        ).scalars().all()
        notif = next((n for n in candidates if (n.external_ids or {}).get(key) == replied_mid), None)
        if notif is None:
            return {"ok": True}

        notif.status = "answered"
        notif.response = text
        notif.answered_at = datetime.now(timezone.utc)
        if notif.task_id:
            task = await db.get(Task, notif.task_id)
            if task and task.status in ("waiting_user", "pending", "ready"):
                number = (
                    await db.execute(
                        select(func.coalesce(func.max(Session.number), 0)).where(Session.task_id == task.id)
                    )
                ).scalar_one() + 1
                db.add(Session(
                    task_id=task.id, agent_id=task.agent_id, number=number, status="planned",
                    scheduled_at=datetime.now(timezone.utc),
                    objective=(f"Reprendre la tâche à la lumière de la réponse de l'utilisateur.\n"
                               f"Question : {notif.content}\nRéponse : {text}"),
                ))
                task.status = "pending"
        await db.commit()
        return {"ok": True}
