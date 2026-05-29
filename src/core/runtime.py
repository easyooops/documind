"""Lazy runtime initialization for API requests."""

from __future__ import annotations

import asyncio

from src.core.bootstrap import bootstrap_status
from src.core.config import settings
from src.core.logging import get_logger, setup_logging

_runtime_initialized = False
_runtime_init_lock = asyncio.Lock()


async def ensure_runtime_initialized() -> None:
    """Initialize runtime resources once without blocking ASGI lifespan startup."""
    global _runtime_initialized
    if _runtime_initialized:
        return

    async with _runtime_init_lock:
        if _runtime_initialized:
            return

        bootstrap_status.reset()
        setup_logging()
        logger = get_logger("src.runtime")
        logger.info(
            "src.starting",
            env=settings.app_env,
            port=settings.app_port,
            log_file=settings.log_file or None,
        )

        from src.infrastructure.database import init_db

        await init_db()
        logger.info("database.initialized")

        bootstrap_status.update(
            phase="icons",
            message="Registering document icons in the local database.",
        )
        _runtime_initialized = True
