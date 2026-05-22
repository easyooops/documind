"""CLI interface for DocuMind - direct document generation from command line."""

from __future__ import annotations

import asyncio
import sys

from src.core.logging import setup_logging, get_logger


async def generate(query: str, format: str = "pptx") -> None:
    """Run the document generation pipeline from CLI."""
    setup_logging()
    logger = get_logger("cli")

    logger.info("cli.generate.start", query=query, format=format)

    from src.engine import _get_format_pipeline
    from src.infrastructure.database import init_db
    from src.schemas.agents import DocuMindState

    await init_db()

    initial_state: DocuMindState = {
        "user_query": query,
        "session_id": "cli-session",
        "template_id": None,
        "conversation_history": [],
        "document_format": format,
        "needs_research": True,
        "template_provided": False,
        "current_phase": "planning",
        "errors": [],
        "retry_count": 0,
        "qa_iterations": 0,
    }

    pipeline = _get_format_pipeline(format)
    result = await pipeline.ainvoke(initial_state)

    output_path = result.get("output_path", "N/A")
    fidelity = result.get("fidelity_scores", [])

    logger.info("cli.generate.complete", output_path=output_path, fidelity_scores=fidelity)
    print(f"\nDocument generated: {output_path}")
    if fidelity:
        print(f"   Fidelity score: {fidelity[-1]:.2f}")


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m src.cli generate \"Your presentation topic\"")
        print("       python -m src.cli generate \"topic\" --format pptx")
        sys.exit(1)

    command = sys.argv[1]

    if command == "generate":
        if len(sys.argv) < 3:
            print("Error: Please provide the document topic.")
            sys.exit(1)

        query = sys.argv[2]
        format = "pptx"

        if "--format" in sys.argv:
            idx = sys.argv.index("--format")
            if idx + 1 < len(sys.argv):
                format = sys.argv[idx + 1]

        asyncio.run(generate(query, format))
    else:
        print(f"Unknown command: {command}")
        print("Available commands: generate")
        sys.exit(1)


if __name__ == "__main__":
    main()
