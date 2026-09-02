"""Sonolo FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    gamification,
    health,
    learn,
    listening,
    microlessons,
    packs,
    progress,
    pronunciation,
    quests,
    review,
    scenarios,
    scorecard,
    sessions,
    users,
    ws,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    if settings is None:
        settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Sonolo API starting: version=%s environment=%s",
            settings.app_version,
            settings.environment,
        )
        yield
        logger.info("Sonolo API shutting down")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(users.router, prefix=settings.api_prefix)
    app.include_router(sessions.router, prefix=settings.api_prefix)
    app.include_router(review.router, prefix=settings.api_prefix)
    app.include_router(quests.router, prefix=settings.api_prefix)
    app.include_router(gamification.router, prefix=settings.api_prefix)
    app.include_router(scenarios.router, prefix=settings.api_prefix)
    app.include_router(packs.router, prefix=settings.api_prefix)
    app.include_router(microlessons.router, prefix=settings.api_prefix)
    app.include_router(pronunciation.router, prefix=settings.api_prefix)
    app.include_router(listening.router, prefix=settings.api_prefix)
    app.include_router(learn.router, prefix=settings.api_prefix)
    app.include_router(scorecard.router, prefix=settings.api_prefix)
    app.include_router(progress.router, prefix=settings.api_prefix)
    app.include_router(ws.router)
    return app


app = create_app()
