# Autonomous Document-Generation Agent

A small autonomous AI agent, exposed as a FastAPI service, that:

1. **Understands** a natural language request (`POST /agent`)
2. **Plans** its own task list — classifies the document type and generates a section-by-section outline
3. **Executes** each planned task — drafts content for every section, using mock data where real data isn't available
4. **Produces** a polished Microsoft Word (`.docx`) file as the final deliverable
5. **Reports back** the plan it executed (with per-step status) plus a summary and a download link

Supported document types (auto-detected from the request): Business Proposal, Meeting Minutes,
Project Plan, Standard Operating Procedure, Technical Design Document, Product Specification,
Business Report.

## Why this counts as "autonomous"

- The agent is **not** given a fixed template. It asks the LLM (or its offline planner) to decide
  the document type, the title, the audience, and the number/order of sections — the resulting
  TODO list is different for every request.
- Each planned section becomes its own **task** with a tracked status
  (`pending → in_progress → done/failed`), executed independently.
- If a section (or even the whole planning step) fails, the agent **degrades gracefully** —
  it logs the failure, substitutes a reasonable fallback, and keeps going rather than crashing.
  This is itself an autonomous decision the agent makes at runtime.
- If the configured LLM is unreachable, the agent **autonomously falls back** to a deterministic,
  offline "mock" generator so the pipeline always completes.

## Architecture

```
app/
  main.py         FastAPI app: POST /agent, GET /download/{filename}, GET /health
  agent.py        The Agent class: plan -> execute -> assemble -> summarize
  llm_client.py   Pluggable LLM backends (Ollama / Groq / Gemini) + offline mock generator
  doc_builder.py  Builds the polished .docx with python-docx (title page, TOC field,
                  headings, bullets, tables, footer page numbers)
  schemas.py      Pydantic request/response models
  config.py       Environment-driven configuration
generated_docs/   Output .docx files (created at runtime)
test_agent.py     Run the agent directly from the CLI, no server needed
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

### Choose an LLM backend (edit `.env`)

The system runs **fully offline with zero cost** using `LLM_PROVIDER=mock` (the default) — a
deterministic, template-driven generator that still produces a complete, well-structured document
with realistic mock data. This means the assignment can be graded without any API key or local
model installed.

For real LLM-generated content, pick one of these free options:

| Provider | `.env` setting | Notes |
|---|---|---|
| **Ollama** (local, free, no key) | `LLM_PROVIDER=ollama` | Install from ollama.com, then `ollama pull llama3.1` and run `ollama serve` |
| **Groq** (free tier, very fast) | `LLM_PROVIDER=groq` + `GROQ_API_KEY=...` | Free key at console.groq.com/keys |
| **Gemini** (free tier) | `LLM_PROVIDER=gemini` + `GEMINI_API_KEY=...` | Free key at aistudio.google.com/apikey |
| **Offline mock** (default) | `LLM_PROVIDER=mock` | No key, no server, no internet required |

Whichever provider you pick, if a call fails for any reason (offline, rate-limited, bad key) the
agent automatically falls back to the offline mock generator rather than failing the request.

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

## Use

```bash
curl -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"request": "Create a project plan for launching a new mobile banking app"}'
```

Example response:

```json
{
  "status": "completed",
  "document_type": "Project Plan",
  "title": "Project plan for launching a new mobile banking app",
  "plan": [
    {"id": 1, "name": "Analyze request and generate document outline", "status": "done", "detail": "Classified as 'Project Plan' with 7 planned sections."},
    {"id": 2, "name": "Draft section: Project Overview", "status": "done", "detail": null},
    "... one task per planned section ...",
    {"id": 9, "name": "Assemble content into Word document", "status": "done", "detail": "38fcf897fe_project-plan....docx"},
    {"id": 10, "name": "Generate final summary", "status": "done", "detail": null}
  ],
  "summary": "The agent analyzed the request, classified it as a 'Project Plan', ...",
  "llm_provider_used": "mock",
  "filename": "38fcf897fe_project-plan-for-launching-a-new-mobile-.docx",
  "download_url": "/download/38fcf897fe_project-plan-for-launching-a-new-mobile-.docx"
}
```

Then download the document:

```bash
curl -o output.docx http://localhost:8000/download/38fcf897fe_project-plan-for-launching-a-new-mobile-.docx
```

## Run without the server (CLI smoke test)

```bash
python test_agent.py "Write meeting minutes for the weekly engineering sync about API rate limiting"
```

## Example requests to try

- `"Create a business proposal for offering managed IT support to a mid-size law firm"`
- `"Write meeting minutes for the weekly engineering sync about API rate limiting"`
- `"Draft a technical design document for a real-time chat microservice"`
- `"Prepare an SOP for onboarding new warehouse staff"`
- `"Write a product specification for a habit-tracking mobile app"`
- `"Prepare a quarterly business report on customer churn"`

## Docker

The image is deliberately minimal — no LibreOffice, no dev tools, just the
runtime deps (`python-docx` builds `.docx` files natively, no external
binary required) — so it comfortably fits a 512MB-RAM free tier.

```bash
docker build -t docx-agent .
docker run -p 8000:8000 -e LLM_PROVIDER=mock docx-agent
curl http://localhost:8000/health
```

Installed dependencies are ~49MB; with a single uvicorn worker the process
typically sits well under 512MB RSS even under load. `--workers 1` in the
Dockerfile's `CMD` is intentional — don't raise it on a 512MB instance,
since each additional worker is a full copy of the app/interpreter.

## Deploy to Render (free tier)

**Option A — Blueprint (one click):** push this repo to GitHub, then in
Render choose **New → Blueprint** and point it at the repo. `render.yaml`
already defines the service (Docker env, free plan, `/health` health check).

**Option B — Manual:**
1. Push this repo to GitHub.
2. Render → **New → Web Service** → connect the repo.
3. Environment: **Docker** (Render auto-detects the `Dockerfile`).
4. Instance type: **Free**.
5. Health check path: `/health`.
6. Environment variables: set `LLM_PROVIDER` (`mock` works out of the box;
   set to `groq` or `gemini` and add the matching `*_API_KEY` for real
   LLM-generated content — both have free tiers).
7. Deploy. Render injects `PORT` automatically; the Dockerfile's `CMD`
   already binds to `${PORT}`, no changes needed.

**Things to know about the free tier:**
- The filesystem is **ephemeral** — `generated_docs/` is wiped on every
  redeploy and on every cold start after the instance spins down from
  inactivity. That's fine here since each request regenerates its own file,
  but download the `.docx` promptly after calling `/agent` rather than
  treating `/download` as long-term storage.
- The free instance **spins down after ~15 min idle** and cold-starts on
  the next request (a few seconds extra latency) — expected, not a bug.
- `ollama` won't work on Render (no local model server there); use `mock`,
  `groq`, or `gemini` in production.

## How this is tested

Three layers, all offline/deterministic (`LLM_PROVIDER=mock`, no network
or API key needed) so they run the same in CI as on a laptop:

1. **`pytest tests/`** — the real test suite (`tests/test_api.py`), run via
   FastAPI's `TestClient` so no server process is needed:
   - API contract: `/health`, `/`, request validation (empty request → 400,
     missing field → 422)
   - End-to-end classification: parametrized over all 6 document types,
     asserting the right `document_type` is inferred and every planned task
     finishes `done` or `failed` (never left `pending`/`in_progress`)
   - File delivery: `/download/{filename}` returns a real, non-trivial
     `.docx` with the correct content-type; path-traversal attempts and
     missing files are rejected correctly
   - **Reflection**: a test monkey-patches the LLM client to return a
     placeholder-laden first draft and asserts the self-check catches it
     and the task log shows `Revised after self-check`
   - **Resilience**: a test forces one section's generation to raise, and
     asserts the run still completes (`completed_with_errors`), only that
     one task is marked `failed`, and the `.docx` is still produced

   ```bash
   pip install -r requirements-dev.txt
   pytest -v
   ```

2. **`test_agent.py`** — a CLI smoke test that runs the agent directly
   (no HTTP layer) and prints the full task log, useful for eyeballing a
   real request quickly.

3. **Manual visual QA** — for any doc-layout change, convert the output to
   PDF and inspect it (`soffice --headless --convert-to pdf`, then
   `pdftoppm`), since `python-docx`'s XML output rendering correctly is
   easy to get subtly wrong (table widths, TOC fields, page breaks) in ways
   `pytest` alone won't catch.

`requirements-dev.txt` (pytest + httpx for `TestClient`) is intentionally
**not** installed in the Docker image — it's dev/CI-only, kept out of the
production footprint.



- The generated Table of Contents uses a real Word field (`TOC`); open the document in Microsoft
  Word and right-click → "Update Field" (or print/print-preview) to populate it, per standard
  Word behavior for dynamic fields.
- Mock data (names, dates, budget figures) is clearly synthetic and used only where real data
  isn't available, per the assignment's allowance for mock data.
