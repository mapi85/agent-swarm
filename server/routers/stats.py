"""Statistiques du tableau de bord : consommation de tokens et timeline des sessions.
Cadré au périmètre de l'utilisateur (l'admin voit tout)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])

_PERIODS = {"all": None, "7d": timedelta(days=7), "24h": timedelta(hours=24)}


def _fill_buckets(raw, bucket, since):
    """Comble les tranches vides pour un histogramme continu et régulier.

    Sans ça, les jours/heures sans activité manquent : les barres se collent et
    la période (7 jours, 24h) ne se lit pas. On génère toutes les tranches de la
    fenêtre (0 token quand rien), du début à maintenant.
    - bucket="hour" -> pas d'1 heure, label "JJ/MM HHh"
    - bucket="day"  -> pas d'1 jour, label "JJ/MM"
    """
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    now = datetime.now(timezone.utc)

    def floor(dt):
        dt = dt.astimezone(timezone.utc)
        return (dt.replace(minute=0, second=0, microsecond=0) if bucket == "hour"
                else dt.replace(hour=0, minute=0, second=0, microsecond=0))

    counts = {floor(r["b"]): (r["t"] or 0, r["s"] or 0) for r in raw if r["b"] is not None}
    # Début de la fenêtre : `since` si défini (7d/24h), sinon la 1re donnée réelle (all).
    if since is not None:
        start = floor(since)
    elif counts:
        start = min(counts)
    else:
        return []

    out = []
    cur = start
    end = floor(now)
    # Garde-fou : au-delà de ~800 tranches (ex. "all" sur des mois en horaire),
    # on ne pré-remplit pas à l'infini — mais day reste raisonnable sur l'échelle du projet.
    max_slots = 800
    while cur <= end and len(out) < max_slots:
        t, s = counts.get(cur, (0, 0))
        if bucket == "hour":
            label = cur.strftime("%d/%m %Hh")
        else:
            label = cur.strftime("%d/%m")
        out.append({"label": label, "t": t, "s": s})
        cur += step
    return out


@router.get("/tokens")
async def token_stats(
    period: str = "all",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delta = _PERIODS.get(period)
    since = datetime.now(timezone.utc) - delta if delta else None

    where = ["tu.user_id = :uid"]   # comptes indépendants (admin compris)
    params: dict = {"uid": user.id}
    if since is not None:
        where.append("tu.ts >= :since")
        params["since"] = since
    w = " AND ".join(where)

    async def rows(sql):
        return (await db.execute(text(sql), params)).mappings().all()

    totals = (await db.execute(text(
        f"SELECT COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
        f"COALESCE(SUM(cached_input_tokens),0) c, COUNT(DISTINCT session_id) s "
        f"FROM token_usage tu WHERE {w}"), params)).mappings().first()
    heaviest = (await db.execute(text(
        f"SELECT COALESCE(MAX(t),0) m FROM (SELECT SUM(input_tokens+output_tokens) t "
        f"FROM token_usage tu WHERE {w} GROUP BY session_id) x"), params)).scalar()

    by_agent = await rows(
        f"SELECT a.name, SUM(tu.input_tokens) i, SUM(tu.output_tokens) o "
        f"FROM token_usage tu JOIN agents a ON a.id=tu.agent_id WHERE {w} "
        f"GROUP BY a.name ORDER BY SUM(tu.input_tokens+tu.output_tokens) DESC LIMIT 20")
    by_provider = await rows(
        f"SELECT COALESCE(p.name,'—') name, SUM(tu.input_tokens) i, SUM(tu.output_tokens) o "
        f"FROM token_usage tu LEFT JOIN providers p ON p.id=tu.provider_id WHERE {w} "
        f"GROUP BY p.name ORDER BY SUM(tu.input_tokens+tu.output_tokens) DESC")
    # Regroupement : 24h -> par heure ; 7j/tout -> par jour.
    bucket = "hour" if period == "24h" else "day"
    raw = await rows(
        f"SELECT date_trunc('{bucket}', tu.ts) b, "
        f"SUM(tu.input_tokens+tu.output_tokens) t, COUNT(DISTINCT tu.session_id) s "
        f"FROM token_usage tu WHERE {w} GROUP BY 1 ORDER BY 1")
    by_time = _fill_buckets(raw, bucket, since)

    total = (totals["i"] or 0) + (totals["o"] or 0)
    sess = totals["s"] or 0
    cached = totals["c"] or 0
    return {
        "input": totals["i"] or 0, "output": totals["o"] or 0, "total": total,
        "cached_input": cached,
        "cache_hit_rate": round(cached / totals["i"] * 100) if totals["i"] else 0,
        "sessions": sess, "avg_per_session": round(total / sess) if sess else 0,
        "heaviest_session": heaviest or 0,
        "by_agent": [dict(r) for r in by_agent],
        "by_provider": [dict(r) for r in by_provider],
        "by_time": [dict(r) for r in by_time],
    }


@router.get("/timeline")
async def timeline(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Sessions dans la fenêtre -12h / +6h (pour la frise du tableau de bord)."""
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=12), now + timedelta(hours=6)
    scope = "AND t.owner_user_id = :uid"   # comptes indépendants (admin compris)
    rows = (await db.execute(text(
        f"""SELECT s.id, s.task_id, s.status, s.objective, s.started_at, s.ended_at, s.scheduled_at,
                   a.name agent, a.category
            FROM sessions s JOIN tasks t ON t.id=s.task_id JOIN agents a ON a.id=s.agent_id
            WHERE COALESCE(s.started_at, s.scheduled_at) BETWEEN :start AND :end {scope}
            ORDER BY a.name, s.id"""),
        {"start": start, "end": end, "uid": user.id})).mappings().all()
    return {
        "start": start.isoformat(), "now": now.isoformat(), "end": end.isoformat(),
        "sessions": [dict(r) for r in rows],
    }
