"""Application FastAPI v2 — socle (chantier 1).

Les routeurs métier (auth, agents, tâches, missions…) seront montés ici
au fil des chantiers suivants.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import text

from . import scheduler
from .config import get_settings
from .db import engine
from .logging_setup import setup_logging
from .routers import (
    agents,
    auth,
    channels,
    missions,
    notifications,
    overview,
    providers,
    resources,
    sessions,
    stats,
    stream,
    tasks,
    usage,
    users,
    webhooks,
)

log = logging.getLogger("swarm")

APP_VERSION = "2.0.0-dev"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    for directory in (settings.agents_dir, settings.tasks_dir, settings.resources_dir):
        directory.mkdir(parents=True, exist_ok=True)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await scheduler.recover_stale_state()
    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(scheduler.scheduler_loop(stop_event))
    log.info("démarrage", extra={"version": APP_VERSION})
    yield
    stop_event.set()
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    await engine.dispose()
    log.info("arrêt")


app = FastAPI(title="Essaim d'agents autonomes", version=APP_VERSION, lifespan=lifespan)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(providers.router)
app.include_router(agents.router)
app.include_router(tasks.router)
app.include_router(missions.router)
app.include_router(sessions.router)
app.include_router(notifications.router)
app.include_router(channels.channels)
app.include_router(channels.smtp_router)
app.include_router(webhooks.router)
app.include_router(stream.router)
app.include_router(resources.router)
app.include_router(overview.router)
app.include_router(stats.router)
app.include_router(usage.router)


@app.get("/healthz")
async def healthz() -> dict:
    """Sonde de vie : vérifie l'accès base. 503 si la base est injoignable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": APP_VERSION, "db": "up"}
    except Exception as exc:  # noqa: BLE001 — la sonde ne doit jamais lever
        log.error("healthz : base injoignable", extra={"error": str(exc)})
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})


# --- Front statique (SPA Vue) : monté en dernier pour ne pas masquer l'API ---
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        """Sert les fichiers du build, avec repli sur index.html pour le routage SPA."""
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
