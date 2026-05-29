"""DocuMind FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.bootstrap import bootstrap_status
from src.core.config import settings
from src.core.logging import get_logger
from src.core.runtime import ensure_runtime_initialized

app = FastAPI(
    title=settings.app_name,
    description=(
        "Agentic AI Document Generation Platform - Create designed native documents "
        "from natural language"
    ),
    version="0.2.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def runtime_init_middleware(request: Request, call_next):
    """Ensure runtime resources are available before handling API requests."""
    await ensure_runtime_initialized()
    return await call_next(request)


# API routers
from src.api.v1.chat import router as chat_router  # noqa: E402
from src.api.v1.documents import router as documents_router  # noqa: E402
from src.api.v1.settings import router as settings_router  # noqa: E402
from src.api.v1.system import router as system_router  # noqa: E402
from src.api.v1.templates import router as templates_router  # noqa: E402
from src.api.v1.users import router as users_router  # noqa: E402

app.include_router(documents_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(templates_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    bootstrap = bootstrap_status.snapshot()
    return {
        "status": "healthy" if bootstrap["ready"] else "bootstrapping",
        "app": settings.app_name,
        "version": "0.2.0",
        "env": settings.app_env,
        "bootstrap": bootstrap,
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
