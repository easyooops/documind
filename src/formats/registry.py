"""Format engine registry - discovers and provides format engines."""

from __future__ import annotations

from src.core.exceptions import ConversionError
from src.core.logging import get_logger
from src.formats.base import FormatEngine

logger = get_logger(__name__)

_registry: dict[str, type[FormatEngine]] = {}


def register_format(engine_class: type[FormatEngine]) -> type[FormatEngine]:
    """Register a format engine class. Can be used as a decorator."""
    format_id = engine_class.format_id.fget(None)  # type: ignore
    _registry[format_id] = engine_class
    logger.debug("format_registry.registered", format_id=format_id)
    return engine_class


def get_format_engine(format_id: str) -> FormatEngine:
    """Get an instantiated format engine by its ID.

    Raises ConversionError if the format is not registered.
    """
    _ensure_loaded()

    engine_class = _registry.get(format_id)
    if not engine_class:
        available = ", ".join(sorted(_registry.keys())) or "(none)"
        raise ConversionError(
            f"Unsupported format: '{format_id}'. Available: {available}"
        )

    return engine_class()


def list_formats() -> list[str]:
    """Return list of registered format IDs."""
    _ensure_loaded()
    return sorted(_registry.keys())


_loaded = False


def _ensure_loaded() -> None:
    """Import all format modules to trigger registration."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    # Import format packages to trigger @register_format decorators
    try:
        import src.formats.pptx  # noqa: F401
    except ImportError:
        pass
    try:
        import src.formats.docx  # noqa: F401
    except ImportError:
        pass
    try:
        import src.formats.md  # noqa: F401
    except ImportError:
        pass
    try:
        import src.formats.pdf  # noqa: F401
    except ImportError:
        pass
    try:
        import src.formats.xlsx  # noqa: F401
    except ImportError:
        pass
    try:
        import src.formats.hwp  # noqa: F401
    except ImportError:
        pass
