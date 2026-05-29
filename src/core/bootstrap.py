"""Runtime bootstrap status shared by startup tasks and readiness APIs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from src.core.logging import get_logger


class BootstrapStatus:
    """Small process-local status holder for first-run setup progress."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state: dict[str, Any] = {}
        self.reset()

    def reset(self) -> None:
        now = _now()
        with self._lock:
            self._state = {
                "ready": False,
                "phase": "starting",
                "message": "Preparing server configuration.",
                "total": 0,
                "completed": 0,
                "created": 0,
                "skipped": 0,
                "failed": 0,
                "error": None,
                "started_at": now,
                "updated_at": now,
            }

    def update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)
            self._state["updated_at"] = _now()

    def update_icon_progress(
        self,
        *,
        total: int,
        completed: int,
        created: int,
        skipped: int,
        failed: int,
    ) -> None:
        self.update(
            phase="icons",
            message="Registering document icons in the local database.",
            total=total,
            completed=completed,
            created=created,
            skipped=skipped,
            failed=failed,
        )

    def mark_ready(self, message: str = "Server configuration is complete.") -> None:
        self.update(ready=True, phase="ready", message=message, error=None)

    def mark_error(self, error: str) -> None:
        self.update(
            ready=False,
            phase="error",
            message="Server configuration failed.",
            error=error[:300],
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
        total = int(state.get("total") or 0)
        completed = int(state.get("completed") or 0)
        state["progress"] = round((completed / total) * 100) if total else 0
        return state


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


bootstrap_status = BootstrapStatus()
_icon_bootstrap_task: asyncio.Task[None] | None = None


def start_icon_bootstrap() -> None:
    """Start icon registry bootstrap once the API is ready to answer polling."""
    global _icon_bootstrap_task
    if _icon_bootstrap_task is not None and not _icon_bootstrap_task.done():
        return
    _icon_bootstrap_task = asyncio.create_task(_run_icon_bootstrap())


async def cancel_icon_bootstrap() -> None:
    """Cancel icon registry bootstrap during application shutdown."""
    if _icon_bootstrap_task is None or _icon_bootstrap_task.done():
        return
    _icon_bootstrap_task.cancel()
    try:
        await _icon_bootstrap_task
    except asyncio.CancelledError:
        pass


async def _run_icon_bootstrap() -> None:
    logger = get_logger("src.bootstrap")
    try:
        from src.utils.iconify import preload_recommended_icons

        icon_stats = await preload_recommended_icons(
            progress_callback=bootstrap_status.update_icon_progress
        )
        logger.info("icons.registry_initialized", **icon_stats)
        bootstrap_status.mark_ready()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("icons.registry_init_failed", error=str(exc)[:200])
        bootstrap_status.mark_error(str(exc))
