# MAIC-UI Batch Course Generation

This guide explains how to generate interactive courseware locally from your own MAIC-UI backend and your own model API credentials.

## What Uses Your API Key?

The batch scripts do **not** call model providers directly. They call the MAIC-UI FastAPI backend.

Configure model credentials in `.env`:

```env
TRANSFER_API_KEY=sk-your-key
TRANSFER_BASE_URL=https://api.siliconflow.cn/v1
TRANSFER_MODEL=glm-4.7
```

Then the backend uses that key when it generates PDF, PPT, or concept courseware. The script-side `.env.batch` only stores the backend URL and MAIC-UI login account.

## Quick Start (Windows)

```cmd
:: 1. Configure your model API key
copy .env.example .env
notepad .env

:: 2. Start MAIC-UI locally
npm run install:all
npm run dev

:: 3. Configure the batch client
copy .env.batch.example .env.batch
notepad .env.batch

:: 4. Register/login and verify backend connectivity
maic-gen setup

:: 5. Generate one course
maic-gen concept examples\concept_derivative.json
maic-gen pdf .\lesson.pdf "Quadratic Functions"
maic-gen ppt .\slides.pptx "Probability Introduction"

:: 6. Generate many courses from a TSV manifest
maic-batch examples\batch_courses.tsv
```

For Docker users:

```cmd
copy .env.example .env
notepad .env
docker compose build
docker compose up -d
```

Use `MAIC_API_BASE=http://127.0.0.1:8927/api` in `.env.batch` when calling through nginx, or `http://127.0.0.1:8000/api` when calling the backend directly.

## Quick Start (Linux / macOS / Git Bash)

```bash
cp .env.example .env
# Edit .env and set TRANSFER_API_KEY / TRANSFER_BASE_URL / TRANSFER_MODEL
npm run install:all
npm run dev

cp .env.batch.example .env.batch
# Edit MAIC_API_BASE, MAIC_EMAIL, MAIC_PASSWORD

MAIC_API_BASE=http://127.0.0.1:8000/api \
  MAIC_EMAIL=test@example.com \
  MAIC_PASSWORD='Test123456' \
  ./scripts/maic_generate_course.sh concept ./examples/concept_derivative.json

./scripts/maic_batch_generate.sh examples/batch_courses.tsv
```

## Architecture

```text
TSV manifest / concept JSON / PDF / PPTX
  |
  |-- maic-gen.cmd / scripts\maic_generate.ps1          (Windows single task)
  |-- maic-batch.cmd / scripts\maic_batch.ps1           (Windows batch)
  |-- scripts/maic_generate_course.sh                   (bash single task)
  |-- scripts/maic_batch_generate.sh                    (bash batch)
  |
  v
MAIC-UI FastAPI backend (uses your .env model API key)
  |
  v
AI generation -> HTML + JSON metadata + logs
```

The scripts call existing backend APIs:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/pdf/upload`
- `POST /api/pdf/concept/upload`
- `POST /api/ppt/upload`
- `POST /api/ppt/documents/{id}/configure`
- status polling and result download endpoints

## .env Configuration

`.env` is read by the backend and contains model-provider credentials.

| Variable | Description |
|---|---|
| `TRANSFER_API_KEY` | Your OpenAI-compatible transfer endpoint API key |
| `TRANSFER_BASE_URL` | Endpoint base URL, for example `https://api.siliconflow.cn/v1` |
| `TRANSFER_MODEL` | Default model used when no task override is supplied |
| `ZHIPU_API_KEY`, `OPENAI_API_KEY`, etc. | Optional provider-specific credentials |

## .env.batch Configuration

`.env.batch` is read by the CLI scripts and contains backend/login settings.

| Variable | Default | Description |
|---|---|---|
| `MAIC_API_BASE` | `http://127.0.0.1:8000/api` | Backend API URL |
| `MAIC_EMAIL` | required | MAIC-UI account email |
| `MAIC_PASSWORD` | required | MAIC-UI account password |
| `MAIC_MODEL` | `glm-4.7` | Default AI model override sent to backend |
| `MAIC_LANGUAGE` | `zh` | Output language (`zh`/`en`) |
| `MAIC_GENERATION_MODE` | `heavy` | PDF mode: `fast` or `heavy` |
| `MAIC_PUBLIC` | `false` | Publish generated document publicly |
| `MAIC_OUT_DIR` | `maic_outputs` | Single-course output directory |
| `MAIC_BATCH_OUT_DIR` | `maic_batch_outputs` | Batch output directory |
| `MAIC_CONTINUE_ON_ERROR` | `true` | Keep processing after one task fails |
| `MAIC_TIMEOUT_SECONDS` | `1800` | Max wait per task |
| `MAIC_PPT_BATCH_SIZE` | `5` | Slides per PPT batch |

## Manifest Format (TSV)

Use tab-separated values with this header:

```text
id    type    input    title    subject    grade    model    public    mode    language
```

| Column | Description |
|---|---|
| `id` | Stable task id for output folders and logs |
| `type` | `pdf`, `ppt`, or `concept` |
| `input` | File path relative to the manifest, or absolute path |
| `title` | Course title; ignored for concept JSON |
| `subject` | Optional subject, e.g. `math` |
| `grade` | Optional grade level, e.g. `10` |
| `model` | Optional model override, e.g. `glm-4.7` |
| `public` | `true` or `false` |
| `mode` | PDF generation mode: `fast` or `heavy` |
| `language` | `zh` or `en` |

Blank lines and lines starting with `#` are ignored.

## Concept JSON Format

```json
{
  "subject": "math",
  "concept_name": "Derivative as rate of change",
  "concept_overview": "Explain the derivative using motion and slope.",
  "mastery_points": "Students can interpret derivative values and units.",
  "design_idea": "Use sliders and prediction questions.",
  "grade_level": 11,
  "description": "High-school calculus micro lesson",
  "interests": "physics,sports",
  "include_exercises": true,
  "include_prerequisites": true
}
```

## Output Structure

```text
maic_batch_outputs/
├── concept_derivative/
│   └── pdf_123/
│       ├── document.json
│       ├── website.json
│       └── website.html
├── logs/
│   └── concept_derivative.log
└── summary.txt
```

## Local Batch Helpers

`ops/local-batch` contains Windows helpers for local experiments with MAIC-UI and OpenMAIC:

- start/stop local services and write logs/PIDs
- run MAIC-UI batch generation against `http://127.0.0.1:8000/api`
- run OpenMAIC batch generation against `http://localhost:3001`
- slice source PDFs by `page_range` before upload
- save generated HTML/JSON/results under ignored `outputs/`

See `ops/local-batch/README-local-batch.md` for details.

## Quality Notes

Generated HTML is validated for a minimum size, learner controls, a canvas visualization, and a `trackEvent(eventType, payload)` logger that writes to `localStorage.maic_learning_events`. If validation fails, the backend records the issue in document metadata and may retry once. Prefer fixing prompt requirements first; blind retries can be expensive on slow models.

## Safety Notes

- Use your own model API key in `.env`; never commit it.
- Use a dedicated low-privilege test account for collaborators.
- Batch generation consumes the host backend's model API quota.
- For large batches, run sequentially first and prefer PostgreSQL over SQLite.
- Do not expose the backend publicly without authentication and rate limits.
