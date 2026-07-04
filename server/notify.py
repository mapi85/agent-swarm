"""Dispatch des notifications vers les canaux externes (email, Telegram), par utilisateur.

- Les canaux appartiennent à un utilisateur ; une notification est routée vers les
  canaux de son destinataire (`notifications.user_id`) selon son type.
- La config des canaux est chiffrée (`config_enc`) ; le SMTP global l'est aussi
  (setting `smtp_config`), avec repli sur les variables d'environnement.
- Les webhooks Telegram sont protégés par un `secret_token` (vérifié à la réception).
"""
import asyncio
import json
import logging
import secrets
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from sqlalchemy import select

from .config import get_settings
from .crypto import decrypt_secret, encrypt_secret
from .db import SessionLocal
from .models import AppSetting, Notification, NotificationChannel

log = logging.getLogger("swarm.notify")


# --- config des canaux (chiffrée) ---

def channel_config(channel: NotificationChannel) -> dict:
    if not channel.config_enc:
        return {}
    try:
        return json.loads(decrypt_secret(channel.config_enc))
    except (ValueError, json.JSONDecodeError):
        return {}


def encode_channel_config(cfg: dict) -> str:
    return encrypt_secret(json.dumps(cfg, ensure_ascii=False))


def new_secret_token() -> str:
    return secrets.token_urlsafe(24)


# --- SMTP global ---

async def _smtp_cfg(db) -> dict:
    settings = get_settings()
    stored = {}
    row = await db.get(AppSetting, "smtp_config")
    if row and row.value:
        stored = dict(row.value)
        if stored.get("password_enc"):
            try:
                stored["password"] = decrypt_secret(stored["password_enc"])
            except ValueError:
                stored["password"] = ""
    return {
        "host": stored.get("host") or settings.smtp_host,
        "port": int(stored.get("port") or settings.smtp_port or 587),
        "user": stored.get("user") or settings.smtp_user,
        "password": stored.get("password") or settings.smtp_password,
        "from_addr": stored.get("from_addr") or settings.smtp_from or settings.smtp_user,
    }


def _send_email_sync(cfg: dict, to: str, subject: str, body: str) -> None:
    if not cfg["host"]:
        raise ValueError("SMTP non configuré")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"] or cfg["user"]
    msg["To"] = to
    html = f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{body}</pre>"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
        s.ehlo()
        try:
            s.starttls()
        except smtplib.SMTPNotSupportedError:
            pass
        if cfg["user"] and cfg["password"]:
            s.login(cfg["user"], cfg["password"])
        s.sendmail(msg["From"], [to], msg.as_string())


# --- Telegram ---

async def send_telegram(token: str, chat_id: str, text: str) -> int | None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
            data = r.json()
            if data.get("ok"):
                return data["result"]["message_id"]
            log.warning("Telegram sendMessage échec", extra={"desc": data.get("description")})
    except Exception as exc:
        log.warning("Telegram send failed", extra={"error": str(exc)})
    return None


async def set_telegram_webhook(token: str, channel_id: int, base_url: str, secret_token: str) -> bool:
    webhook_url = f"{base_url.rstrip('/')}/api/webhooks/telegram/{channel_id}"
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"url": webhook_url, "secret_token": secret_token})
            data = r.json()
            if not data.get("ok"):
                log.warning("Telegram setWebhook échec", extra={"desc": data.get("description")})
            return bool(data.get("ok"))
    except Exception as exc:
        log.warning("Telegram setWebhook failed", extra={"error": str(exc)})
        return False


# --- formatage ---

def _fmt(notif: Notification) -> tuple[str, str]:
    emoji = "❓" if notif.type == "question" else "🔔"
    label = "Question" if notif.type == "question" else "Alerte"
    subject = f"{emoji} [{label}] L'Essaim"
    reply = "\n\n↩ *Réponds à ce message* pour répondre à l'agent." if notif.type == "question" else ""
    body = f"{emoji} *{label}*\n\n{notif.content}{reply}"
    return subject, body


# --- dispatch ---

async def dispatch_pending() -> None:
    """Envoie les notifications non encore dispatchées vers les canaux de leur destinataire."""
    async with SessionLocal() as db:
        notifs = (
            await db.execute(
                select(Notification).where(Notification.channel_dispatched.is_(False))
                .order_by(Notification.id).limit(50)
            )
        ).scalars().all()
        if not notifs:
            return
        smtp = None
        for notif in notifs:
            use_field = NotificationChannel.use_for_questions if notif.type == "question" \
                else NotificationChannel.use_for_alerts
            channels = (
                await db.execute(
                    select(NotificationChannel).where(
                        NotificationChannel.owner_user_id == notif.user_id,
                        NotificationChannel.enabled.is_(True),
                        use_field.is_(True),
                    )
                )
            ).scalars().all()
            subject, body = _fmt(notif)
            external_ids: dict = {}
            for ch in channels:
                cfg = channel_config(ch)
                try:
                    if ch.type == "email" and cfg.get("to"):
                        if smtp is None:
                            smtp = await _smtp_cfg(db)
                        await asyncio.get_event_loop().run_in_executor(
                            None, _send_email_sync, smtp, cfg["to"], subject, body.replace("*", ""))
                    elif ch.type == "telegram" and cfg.get("bot_token") and cfg.get("chat_id"):
                        mid = await send_telegram(cfg["bot_token"], cfg["chat_id"], body)
                        if mid is not None:
                            external_ids[f"t_{ch.id}"] = mid
                except Exception as exc:
                    log.warning("dispatch canal échec", extra={"channel_id": ch.id, "error": str(exc)})
            notif.channel_dispatched = True
            notif.external_ids = external_ids or None
        await db.commit()


async def send_test(channel: NotificationChannel, db) -> str:
    cfg = channel_config(channel)
    if channel.type == "email":
        if not cfg.get("to"):
            return "Adresse email manquante."
        try:
            smtp = await _smtp_cfg(db)
            await asyncio.get_event_loop().run_in_executor(
                None, _send_email_sync, smtp, cfg["to"], "✅ Test — L'Essaim",
                "Ce message confirme que les notifications email fonctionnent.")
            return f"Email de test envoyé à {cfg['to']}."
        except Exception as exc:
            return f"Erreur SMTP : {exc}"
    if channel.type == "telegram":
        if not cfg.get("bot_token") or not cfg.get("chat_id"):
            return "Token bot ou Chat ID manquant."
        mid = await send_telegram(cfg["bot_token"], cfg["chat_id"],
                                  "✅ *Test — L'Essaim*\nLes notifications Telegram fonctionnent.")
        return f"Message Telegram envoyé (id: {mid})." if mid else "Échec d'envoi (vérifie token et chat_id)."
    return "Type de canal inconnu."
