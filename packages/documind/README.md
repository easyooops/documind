# DocuMind SDK

**Korean:** [`README.ko.md`](README.ko.md)

Lightweight Python SDK for AI document generation.

Install `documind` when you want to generate native document files directly
from Python.

## Installation

```bash
pip install documind
```

Provider integration dependencies for OpenAI, Bedrock, Anthropic, Gemini, and
Vertex AI are included in the default installation.

## Quick Start

```python
import asyncio
from documind import generate_document, init

init(
    llm_provider="openai",
    openai_api_key="sk-...",
    default_llm_model="gpt-4o",
    storage_local_path="./outputs",
)

async def main():
    result = await generate_document(
        query="Create an AI document automation proposal.",
        document_type="pptx",
        locale="en",
    )
    print(result.output_path)

asyncio.run(main())
```

## SDK API Reference

Public imports:

```python
from documind import (
    DocuMind,
    GenerationRequest,
    GenerationResult,
    ImageAttachment,
    TemplateInput,
    configure,
    generate_document,
    init,
    stream_document,
)
```

### Engine Configuration

Use `init(**config)` for process-wide defaults, or pass the same values to
`DocuMind(...)` for one engine instance.

| Option | Type / Example | Description |
|--------|----------------|-------------|
| `llm_provider` | `"openai"` | Provider route. Supported values include `openai`, `anthropic`, `azure`, `bedrock`, `gcp_vertex`, `gemini`, `ollama`, `vllm`, `custom`. |
| `use_default_models` | `True` | If true, agents use the default model names below. |
| `default_llm_model` | `"gpt-4o"` | Text generation model. |
| `default_vlm_model` | `"gpt-4o"` | Accepted for vision-model compatibility. |
| `default_image_model` | `"dall-e-3"` | Image asset model. If unavailable, generation continues without image assets. |
| `storage_local_path` | `"./outputs"` | Output directory for generated documents, HTML previews, and image assets. |
| `log_level` / `log_file` | `"INFO"` / path | Logging controls. |
| `openai_api_key` | `"sk-..."` | OpenAI credential. |
| `openai_base_url` | URL | OpenAI-compatible endpoint override. |
| `anthropic_api_key` | string | Anthropic credential. |
| `google_api_key` | string | Gemini API key. |
| `gcp_project_id`, `gcp_location`, `google_application_credentials` | strings | Vertex AI settings. |
| `aws_profile`, `aws_region`, `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_role_arn` | strings | Bedrock settings. |
| `azure_openai_api_key`, `azure_openai_endpoint`, `azure_openai_api_version`, `azure_openai_deployment` | strings | Azure OpenAI settings. |
| `custom_llm_base_url`, `custom_llm_api_key`, `custom_llm_model_name` | strings | OpenAI-compatible custom provider settings. |
| `preload_icons` | `False` | Optional icon asset warm-up. |
| `icon_preload_limit` | integer | Optional icon warm-up limit. |

### Document Generation

`generate_document(...)` is the simplest one-shot helper. For repeated calls,
create `DocuMind(...)` once and call `engine.generate(...)`.

```python
from documind import DocuMind, ImageAttachment, TemplateInput

engine = DocuMind(
    llm_provider="openai",
    openai_api_key="sk-...",
    storage_local_path="./outputs",
)

result = await engine.generate(
    query="Create a customer onboarding deck.",
    format="pptx",
    locale="en",
    template=TemplateInput(path="./template.pptx"),
    images=[
        ImageAttachment(path="./product.png", description="Product screenshot"),
    ],
    needs_research=False,
)
```

| Argument | Type / Default | Description |
|----------|----------------|-------------|
| `query` | required string | Natural-language document goal. |
| `format` | `"pptx"` | Output format for `engine.generate`. One of `pptx`, `docx`, `xlsx`, `md`, `hwp`. |
| `document_type` | optional | Alias for `format`; useful when adapting external input models. |
| `template_id` | optional string | Compatibility hook for template identifiers. |
| `template` | path, bytes, dict, `TemplateInput` | Optional native/template file. |
| `images` | list of paths, bytes, dicts, `ImageAttachment` | Optional image evidence for planning and visual references. |
| `session_id` | optional string | Caller-provided correlation/session ID. |
| `locale` | `"ko"` | Locale hint. Output language is also inferred from `query`. |
| `needs_research` | `None` | `True` forces research, `False` skips it, `None` lets DocuMind infer intent. |
| `preload_icons` | optional bool | Per-call icon warm-up toggle. |
| `**options` | dict | Forward-compatible pipeline options. Stable SDK callers should prefer the explicit arguments above. |

### Structured Input

```python
from documind import GenerationRequest

generation_input = GenerationRequest(
    query="Create a weekly report.",
    document_type="docx",
    locale="en",
    needs_research=False,
    options={"template_id": "internal-template-id"},
)

result = await engine.generate_from_request(generation_input)
```

| `GenerationRequest` field | Type / Default | Description |
|---------------------------|----------------|-------------|
| `query` | required string | Natural-language document goal. |
| `document_type` | `"pptx"` | `pptx`, `docx`, `xlsx`, `md`, or `hwp`. |
| `template` | `None` | Path, bytes, dict, or `TemplateInput`. |
| `images` | `[]` | Image evidence list. |
| `session_id` | `None` | Correlation/session ID. |
| `locale` | `"ko"` | Locale hint. |
| `needs_research` | `None` | Research routing toggle. |
| `stream` | `False` | Streaming marker for external adapters. Use `generate_stream` for SDK streaming. |
| `options` | `{}` | Additional pipeline options, including `template_id`. |

| Helper type | Fields |
|-------------|--------|
| `TemplateInput` | `path`, `content`, `filename` |
| `ImageAttachment` | `path`, `content`, `filename`, `mime_type`, `role="content_reference"`, `description` |

`GenerationResult` exposes `success`, `output_path`, `output_bytes`,
`document_type`, `mime_type`, `fidelity_scores`, `slide_count`, `errors`,
`metadata`, and `to_dict()`.

## Supported Formats

| Format | Output |
|--------|--------|
| `pptx` | Native PowerPoint file |
| `docx` | Native Word file |
| `xlsx` | Native Excel workbook |
| `md` | Markdown document |
| `hwp` | HWPX document |

## Streaming

```python
from documind import DocuMind, GenerationRequest

engine = DocuMind(llm_provider="openai", openai_api_key="sk-...")
generation_input = GenerationRequest(query="Create a weekly report.", document_type="docx")

async for event in engine.generate_stream(generation_input):
    print(event.to_sse())
```

## Build From Source

```bash
cd packages/documind
python scripts/sync_runtime.py
python -m build
```

Publishing instructions are in [`PUBLISHING.md`](PUBLISHING.md) and
[`PUBLISHING.ko.md`](PUBLISHING.ko.md).

## License

Apache-2.0
