"""DocuMind — FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.config import settings
from src.core.logging import setup_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — startup and shutdown."""
    setup_logging()
    logger = get_logger("src.main")
    logger.info(
        "src.starting",
        env=settings.app_env,
        port=settings.app_port,
        log_file=settings.log_file or None,
    )

    from src.infrastructure.database import init_db
    await init_db()
    logger.info("database.initialized")

    try:
        from src.utils.iconify import preload_recommended_icons

        icon_stats = await preload_recommended_icons()
        logger.info("icons.registry_initialized", **icon_stats)
    except Exception as exc:
        logger.warning("icons.registry_init_failed", error=str(exc)[:200])

    yield

    from src.infrastructure.database import close_db
    await close_db()
    logger.info("src.shutdown")


app = FastAPI(
    title=settings.app_name,
    description="Agentic AI Document Generation Platform - Create designed native documents from natural language",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
from src.api.v1.documents import router as documents_router
from src.api.v1.chat import router as chat_router
from src.api.v1.templates import router as templates_router
from src.api.v1.settings import router as settings_router
from src.api.v1.users import router as users_router

app.include_router(documents_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(templates_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": "0.2.0",
        "env": settings.app_env,
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and log a single line instead of a giant traceback."""
    _logger = get_logger("src.error")
    _logger.error(
        "unhandled_exception",
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
        error=str(exc)[:200],
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/")
async def root():
    """Root endpoint with API documentation links."""
    return {
        "app": settings.app_name,
        "description": "Agentic AI Document Generation Platform",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
    }
