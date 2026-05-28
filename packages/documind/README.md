# DocuMind SDK

PyPI package for API-free AI document generation.

Korean documentation: [README.ko.md](README.ko.md)

```bash
pip install documind
```

```python
import asyncio
from documind import init, generate_document

init(
    llm_provider="openai",
    openai_api_key="sk-...",
    default_llm_model="gpt-4o",
    default_vlm_model="gpt-4o",
    storage_local_path="./outputs",
)

async def main():
    result = await generate_document(
        query="Create an AI document automation proposal.",
        document_type="pptx",
        template="./templates/company-template.pptx",
        images=["./inputs/reference.png"],
        locale="ko",
    )
    print(result.output_path)

asyncio.run(main())
```

Supported formats: `pptx`, `docx`, `pdf`, `md`, `xlsx`, `hwp` (HWPX).

## Source layout

The runtime source of truth is the repository root `src` directory. The `packages/documind/src/src` tree is a generated build mirror created by `python scripts/sync_runtime.py` before publishing, so do not edit it directly.

Streaming:

```python
from documind import DocuMind, GenerationRequest

engine = DocuMind(llm_provider="openai", openai_api_key="sk-...")
request = GenerationRequest(query="Create a weekly report.", document_type="docx")

async for event in engine.generate_stream(request):
    print(event.to_sse())
```

Publishing instructions are in [PUBLISHING.ko.md](PUBLISHING.ko.md).
