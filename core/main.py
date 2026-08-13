"""Hermes entry point.

FastAPI app whose lifespan:
  1. initializes the agent + connectors,
  2. runs a health check,
  3. sends an auto startup brief to Telegram,
  4. starts the Telegram bot (polling) in the background.

Exposes /health for Docker healthchecks.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from config.settings import settings
from core.agent import HermesAgent
from core.briefing import BriefingService
from core.telegram_bot import TelegramInterface

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("hermes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ============ STARTUP ============
    logger.info("🚀 Hermes waking up...")
    agent = HermesAgent()
    app.state.agent = agent

    briefing = BriefingService(agent)
    app.state.briefing = briefing

    health = await agent.health_check_all()
    app.state.health = health
    logger.info("📊 Health: %s", health)

    telegram = None
    if settings.telegram_token:
        telegram = TelegramInterface(agent, briefing)
        tg_app = telegram.build()
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        app.state.telegram = telegram
        logger.info("🤖 Telegram bot polling dimulai.")

        # Auto startup brief.
        try:
            brief_text = await briefing.startup_brief(health)
            await telegram.send_startup_brief(brief_text)
        except Exception:  # noqa: BLE001
            logger.exception("Startup brief gagal")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN kosong — bot tidak dijalankan.")

    logger.info("✅ Hermes ready & standby")

    try:
        yield
    finally:
        # ============ SHUTDOWN ============
        logger.info("💤 Hermes going to sleep...")
        if telegram and telegram.app:
            try:
                await telegram.app.updater.stop()
                await telegram.app.stop()
                await telegram.app.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("Error saat mematikan Telegram")
        await agent.close()


_docs = None if settings.disable_api_docs else "/docs"
_redoc = None if settings.disable_api_docs else "/redoc"
_openapi = None if settings.disable_api_docs else "/openapi.json"

app = FastAPI(
    title="Hermes Agent",
    lifespan=lifespan,
    docs_url=_docs,
    redoc_url=_redoc,
    openapi_url=_openapi,
)


def _authorize_health(x_hermes_health_token: str | None) -> None:
    if not settings.health_token:
        return
    if x_hermes_health_token != settings.health_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health(x_hermes_health_token: str | None = Header(default=None)):
    _authorize_health(x_hermes_health_token)
    payload = {"status": "ok"}
    # Avoid leaking connector inventory on public/unauthenticated deployments.
    if settings.health_token or settings.hermes_env in {"development", "dev", "local", "test"}:
        payload["connectors"] = getattr(app.state, "health", {})
    return payload


@app.get("/")
async def root():
    return {"name": "Hermes Agent", "status": "running"}


@app.middleware("http")
async def block_sensitive_paths(request: Request, call_next):
    """Refuse probing of common secret/config paths."""
    path = request.url.path.casefold()
    blocked_prefixes = ("/.env", "/credentials", "/config/")
    if any(path == item.rstrip("/") or path.startswith(item) for item in blocked_prefixes):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return await call_next(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
