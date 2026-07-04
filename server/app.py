"""Application FastAPI v2 — socle (chantier 1).

Les routeurs métier (auth, agents, tâches, missions…) seront montés ici
au fil des chantiers suivants.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from .config import get_settings
from .db import engine
from .logging_setup import setup_logging
from .routers import auth, users

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
    log.info("démarrage", extra={"version": APP_VERSION})
    yield
    await engine.dispose()
    log.info("arrêt")


app = FastAPI(title="Essaim d'agents autonomes", version=APP_VERSION, lifespan=lifespan)
app.include_router(auth.router)
app.include_router(users.router)


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
