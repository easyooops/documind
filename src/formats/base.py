"""Abstract base classes for format engines and document renderers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class DocumentRenderer(ABC):
    """Abstract document renderer - produces final output file from classified elements."""

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Document format identifier (pptx, docx, pdf, md, html)."""
        ...

    @property
    @abstractmethod
    def mime_type(self) -> str:
        """MIME type of the output document."""
        ...

    @property
    @abstractmethod
    def file_extension(self) -> str:
        """File extension including the dot."""
        ...

    @abstractmethod
    async def render(self, slides_data: list[dict], output_dir: Path) -> Path:
        """Render classified elements into the target document format.

        Args:
            slides_data: List of slide dicts with elements and CSS categories
            output_dir: Directory to write the output file

        Returns:
            Path to the generated document file
        """
        ...


class FormatEngine(ABC):
    """Abstract format engine - wraps renderer + mappers + QA for a given format.

    Each format (pptx, docx, md, html) implements this interface.
    The conversion pipeline delegates to the appropriate FormatEngine
    without needing to know format-specific details.
    """

    @property
    @abstractmethod
    def format_id(self) -> str:
        """Unique identifier for this format (e.g., 'pptx', 'docx', 'md', 'html')."""
        ...

    @property
    @abstractmethod
    def renderer(self) -> DocumentRenderer:
        """Get the document renderer for this format."""
        ...

    @abstractmethod
    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        """Render classified elements into the target document format.

        Args:
            classified_elements: Slides with classified CSS elements
            output_dir: Output directory

        Returns:
            Path to the generated document
        """
        ...

    @abstractmethod
    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """Validate output quality against the original HTML reference.

        Args:
            output_path: Path to the generated document
            reference_html: Original HTML slides for comparison

        Returns:
            Fidelity score between 0.0 and 1.0
        """
        ...


class CSSMapper(ABC):
    """Abstract base for CSS property to format-native conversion."""

    @property
    @abstractmethod
    def css_property(self) -> str:
        """The CSS property this mapper handles."""
        ...

    @abstractmethod
    def map(self, value: str, context: dict | None = None) -> str | dict:
        """Convert CSS value to format-native representation.

        Args:
            value: The computed CSS property value
            context: Optional element context (dimensions, neighbors)

        Returns:
            Format-native representation (XML string, dict, etc.)
        """
        ...

    def can_handle(self, value: str) -> bool:
        """Check if this mapper can handle the given value."""
        return True


class CompensationHandler(ABC):
    """Abstract base for Category B CSS property compensation.

    Category B properties cannot be directly mapped to the target format
    but can be approximated using multiple shapes or effects.
    """

    @property
    @abstractmethod
    def css_property(self) -> str:
        """The CSS property this handler compensates for."""
        ...

    @property
    def needs_context_decision(self) -> bool:
        """Whether LLM should choose the compensation strategy."""
        return False

    @abstractmethod
    async def execute(self, element: dict, **kwargs) -> list[dict]:
        """Generate compensating shape specifications.

        Args:
            element: Element data with position, styles, and content
            **kwargs: Additional context

        Returns:
            List of shape spec dicts to replace the original element
        """
        ...
