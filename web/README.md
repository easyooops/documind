# DocuMind Web

Next.js frontend for the DocuMind document generation platform. Static export (`output: 'export'`) for deployment on any static host.

## Quick Start

From the **repository root** (recommended — starts API + web together):

```bash
npm run install:all   # once, from repo root
npm run dev
```

Platform notes (Windows PATH, venv, `PYTHON=py`): **[../docs/SETUP.md](../docs/SETUP.md)**.

From this directory only (API must already run on port 8000):

```bash
npm install
npm run dev
```

## Environment

Create or edit `.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Static Build

```bash
npm run build
```

Output is written to `out/` and can be deployed to Vercel, Cloudflare Pages, S3 + CDN, or any static file server. Point `NEXT_PUBLIC_API_URL` at your production API at build time.

## Stack

- Next.js 14 (App Router, static export)
- Tailwind CSS + shadcn-style UI primitives
- Zustand (client state)
- Framer Motion (animations)
- Lucide React (icons)

## Features

- User identification (name + email, no RBAC)
- Chat-based document requests with SSE progress
- Template attachment (PPTX/DOCX/PDF)
- Output format selector (PPTX, DOCX, PDF, MD, XLSX)
- Document preview panel with download, version history, and fullscreen view
- Session history sidebar
