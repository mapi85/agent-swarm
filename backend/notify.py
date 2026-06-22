"""Dispatch des notifications vers les canaux externes (email, Telegram)."""
import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from . import config, db

log = logging.getLogger("swarm.notify")


def _smtp_cfg() -> dict:
    stored = db.get_setting("smtp_config", {})
    return {
        "host": stored.get("host") or config.SMTP_HOST,
        "port": int(stored.get("port") or config.SMTP_PORT or 587),
        "user": stored.get("user") or config.SMTP_USER,
        "password": stored.get("password") or config.SMTP_PASSWORD,
        "from_addr": stored.get("from_addr") or config.SMTP_FROM,
    }


def _send_email_sync(to: str, subject: str, body: str) -> None:
    cfg = _smtp_cfg()
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


async def _send_telegram(token: str, chat_id: str, text: str) -> int | None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
            data = r.json()
            if data.get("ok"):
                return data["result"]["message_id"]
            log.warning("Telegram sendMessage: %s", data.get("description"))
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)
    return None


def _fmt(notif_type: str, content: str, agent_name: str) -> tuple[str, str]:
    emoji = "❓" if notif_type == "question" else "🔔"
    label = "Question" if notif_type == "question" else "Alerte"
    subject = f"{emoji} [{label}] {agent_name} — L'Essaim"
    reply = "\n\n↩ *Répondez à ce message* pour répondre à l'agent." if notif_type == "question" else ""
    body = f"{emoji} *{label} de {agent_name}*\n\n{content}{reply}"
    return subject, body


async def dispatch_pending() -> None:
    notifs = db.undispatched_notifications()
    if not notifs:
        return
    for notif in notifs:
        channels = db.channels_for_dispatch(notif["agent_id"], notif["type"])
        if not channels:
            db.mark_notification_channel_dispatched(notif["id"], {})
            continue
        subject, body = _fmt(notif["type"], notif["content"], notif["agent_name"])
        external_ids: dict = {}
        for ch in channels:
            cfg = ch["config"]
            try:
                if ch["type"] == "email":
                    to = cfg.get("to", "")
                    if to:
                        plain = body.replace("*", "")
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, _send_email_sync, to, subject, plain)
                elif ch["type"] == "telegram":
                    token = cfg.get("bot_token", "")
                    chat_id = cfg.get("chat_id", "")
                    if token and chat_id:
                        mid = await _send_telegram(token, chat_id, body)
                        if mid is not None:
                            external_ids[f"t_{ch['id']}"] = mid
            except Exception as exc:
                log.warning("Canal %s (%s) erreur dispatch: %s", ch["id"], ch["type"], exc)
        db.mark_notification_channel_dispatched(notif["id"], external_ids)


async def send_test(channel: dict) -> str:
    cfg = channel["config"]
    ctype = channel["type"]
    if ctype == "email":
        to = cfg.get("to", "")
        if not to:
            return "Adresse email manquante."
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _send_email_sync, to,
                                       "✅ Test — L'Essaim",
                                       "✅ Ce message confirme que les notifications email fonctionnent.")
            return f"Email de test envoyé à {to}."
        except Exception as exc:
            return f"Erreur SMTP : {exc}"
    elif ctype == "telegram":
        token = cfg.get("bot_token", "")
        chat_id = cfg.get("chat_id", "")
        if not token or not chat_id:
            return "Token bot ou Chat ID manquant."
        mid = await _send_telegram(token, chat_id,
                                   "✅ *Test — L'Essaim*\nLes notifications Telegram fonctionnent.")
        return f"Message Telegram envoyé (id: {mid})." if mid is not None else "Échec d'envoi Telegram (vérifiez token et chat_id)."
    return "Type de canal inconnu."


async def register_telegram_webhook(token: str, channel_id: int, base_url: str) -> bool:
    webhook_url = f"{base_url.rstrip('/')}/api/webhooks/telegram/{channel_id}"
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"url": webhook_url})
            data = r.json()
            ok = bool(data.get("ok"))
            if not ok:
                log.warning("Telegram setWebhook: %s", data.get("description"))
            return ok
    except Exception as exc:
        log.warning("Telegram setWebhook failed: %s", exc)
        return False
