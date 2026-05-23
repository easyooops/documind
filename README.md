# DocuMind — Agentic AI Document Generation Platform

> End-to-end platform where 12 specialized agents plan, design, generate, and validate expert-level documents from natural language requests.

## Quick Start

**Platform-specific steps (Windows / Linux / macOS):** see **[docs/SETUP.md](docs/SETUP.md)**.

### 1. Prerequisites

- Python **3.11+** and Node.js **18+**
- Recommended: Python virtual environment (`.venv`)

### 2. Install (from repo root)

**Windows (PowerShell)** — after activating venv if you use one:

```powershell
copy .env.example .env
npm run install:all
```

**Linux / macOS:**

```bash
cp .env.example .env
python3 -m venv .venv && source .venv/bin/activate
npm run install:all
```

`install:all` installs Python packages, Playwright Chromium, and `web/` npm dependencies using cross-platform Node scripts (`scripts/install-all.mjs`).

### 3. Environment

Edit `.env` and set your LLM API key. For the web UI, `web/.env.local` should contain:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 4. Run (backend + web UI)

```bash
npm run dev
```

| Service | URL |
|---------|-----|
| Web UI | http://localhost:3000 |
| API | http://localhost:8000 |
| OpenAPI docs | http://localhost:8000/docs |

Uses `python -m uvicorn` (works on Windows without adding `uvicorn` to PATH). Stop with `Ctrl+C`. On re-run, `npm run dev` stops old listeners and picks the next free port if 8000/3000 are stuck (`npm run dev:kill` to clean up manually).

**Backend logs:** written to `data/logs/documind.log` as compact UTF-8 JSON lines and echoed in the terminal where `npm run dev` runs. File logs rotate daily at UTC midnight and keep `LOG_BACKUP_COUNT` days (default: 14). Set `LOG_FILE=` in `.env` to disable the file. For AWS Bedrock, `npm run install:all` installs `langchain-aws` via the `bedrock` extra; if you installed earlier, run `pip install -e ".[bedrock]"`.

```bash
npm run dev:api   # Backend only
npm run dev:web   # Frontend only
```

**Windows:** if `python` is not on PATH, use `set PYTHON=py` then `npm run dev`. See [docs/SETUP.md](docs/SETUP.md).

### 4. CLI (API-free generation)

```bash
python -m src.cli generate "12-slide cloud migration proposal for executives"
```

---

## Project Structure

```
documind/
├── package.json              # Root scripts (dev, install:all)
├── pyproject.toml            # Python package + dependencies
├── .env.example
│
├── web/                      # Next.js static-export UI
│   ├── package.json
│   └── src/
│
├── src/
│   ├── main.py               # FastAPI entry point
│   ├── cli.py                # CLI interface
│   ├── core/                 # Config, logging, exceptions
│   ├── schemas/              # Pydantic schemas
│   ├── infrastructure/       # DB, storage, LLM adapters
│   ├── agents/               # LangGraph agent pipeline
│   ├── conversion/           # HTML → OOXML conversion
│   └── api/v1/               # REST API routes
│       ├── documents.py
│       ├── chat.py
│       ├── templates.py
│       ├── settings.py
│       └── users.py
│
└── data/                     # Runtime data (gitignored)
    ├── documind.db
    ├── templates/
    └── outputs/
```

---

## Architecture

### 12-Agent Pipeline

```
User request
    │
    ▼
┌─ Planning ──────────────────────────────────────────┐
│ Research → Narrative → Content Writer → Audience    │
└─────────────────────────┬───────────────────────────┘
                          ▼
┌─ Design ────────────────────────────────────────────┐
│ Template → Layout → Style → Asset Plan                │
└─────────────────────────┬───────────────────────────┘
                          ▼
┌─ Generation ────────────────────────────────────────┐
│ Code Agent (parallel) → Consistency → Validation      │
└─────────────────────────┬───────────────────────────┘
                          ▼
┌─ Conversion & QA ─────────────────────────────────────┐
│ Conversion Engine → QA Critic (VLM) → Export          │
└─────────────────────────┬───────────────────────────┘
                          ▼
                    Final document file
```

### Tech Stack

| Layer | Technology |
|-------|------------|
| API | FastAPI + Uvicorn |
| Agents | LangGraph + LangChain |
| LLM | OpenAI, Anthropic, Ollama, and more |
| Documents | python-pptx, lxml |
| Rendering | Playwright (Chromium) |
| Database | SQLAlchemy (SQLite / PostgreSQL) |
| Web UI | Next.js 14, Tailwind, Zustand |

### Extensibility

- **Formats**: Implement `DocumentRenderer` to add new output types
- **LLM**: Swap providers via `LLMProvider` / config
- **Storage**: `StorageBackend` for local, S3, GCS
- **Agents**: Add or remove LangGraph nodes as needed

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/documents/generate` | Start document generation |
| GET | `/api/v1/documents/{id}/status` | Job status |
| GET | `/api/v1/documents/{id}/download` | Download file |
| GET | `/api/v1/documents/{id}/versions` | Version history |
| GET | `/api/v1/documents/{id}/preview` | HTML preview |
| POST | `/api/v1/chat/sessions` | Create chat session |
| POST | `/api/v1/chat/sessions/{id}/messages/stream` | SSE streaming generation |
| POST | `/api/v1/templates/upload` | Upload template |
| POST | `/api/v1/users/identify` | Identify user (name + email) |
| GET | `/api/v1/users/{id}/sessions` | List user sessions |

---

## Development

```bash
# Lint (Python)
npm run lint

# Format (Python)
npm run format

# DB migrations
npm run db:init

# Build static web export
npm run build:web
```

---

## License

MIT — runtime dependencies use MIT/Apache-2.0/BSD-compatible licenses only.
