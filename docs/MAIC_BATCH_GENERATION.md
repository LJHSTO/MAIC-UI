# MAIC-UI Batch Course Generation

## Quick Start (Windows)

```cmd
:: 1. One-time setup
copy .env.batch.example .env.batch
notepad .env.batch            :: set MAIC_API_BASE to host LAN IP
maic-gen setup                :: register account & test connectivity

:: 2. Single course
maic-gen concept examples\concept_derivative.json
maic-gen pdf .\lesson.pdf "Quadratic Functions"
maic-gen ppt .\slides.pptx "Probability Introduction"

:: 3. Batch generation
maic-batch examples\batch_courses.tsv
```

## Quick Start (Linux / macOS / Git Bash)

```bash
# 1. Prepare config
cp .env.batch.example .env.batch
# Edit MAIC_API_BASE, MAIC_EMAIL, MAIC_PASSWORD

# 2. One-time setup (register if needed)
MAIC_API_BASE=http://HOST_IP:8000/api \
  MAIC_EMAIL=test@example.com \
  MAIC_PASSWORD='Test123456' \
  ./scripts/maic_generate_course.sh help

# 3. Single course
./scripts/maic_generate_course.sh concept ./examples/concept_derivative.json
./scripts/maic_generate_course.sh pdf ./lesson.pdf "Quadratic Functions"
./scripts/maic_generate_course.sh ppt ./slides.pptx "Probability Introduction"

# 4. Batch generation
./scripts/maic_batch_generate.sh examples/batch_courses.tsv
```

## Architecture

```
TSV manifest / concept JSON / PDF / PPTX
  ┃
  ┣━━ maic-gen.cmd / maic_batch.ps1     (Windows)
  ┣━━ maic_generate_course.sh / maic_batch_generate.sh  (bash)
  ┃
  ▼
MAIC-UI FastAPI backend  (run by host on 0.0.0.0:8000)
  ┃
  ▼
AI generation → download HTML + JSON metadata + logs
```

The scripts call existing backend APIs without reimplementing anything:

- `POST /api/auth/login`
- `POST /api/pdf/upload`
- `POST /api/pdf/concept/upload`
- `POST /api/ppt/upload`
- `POST /api/ppt/documents/{id}/configure`
- Status polling & result download endpoints

## Host Machine Setup

The person with MAIC-UI configured runs the backend:

```cmd
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Other machines on the same LAN use `http://YOUR_LAN_IP:8000/api` as the API base.

For remote collaborators, expose via Cloudflare Tunnel or ngrok and use `https://YOUR_TUNNEL_URL/api`.

## Manifest Format (TSV)

Tab-separated with these columns:

```
id    type    input    title    subject    grade    model    public    mode    language
```

| Column | Description |
|--------|-------------|
| id | Stable task id for output folders and logs |
| type | `pdf`, `ppt`, or `concept` |
| input | File path (relative to manifest, or absolute) |
| title | Course title (ignored for concept) |
| subject | Optional, e.g. `math` |
| grade | Optional grade level, e.g. `10` |
| model | Optional model override, e.g. `glm-4.7` |
| public | `true` or `false` |
| mode | PDF generation mode: `fast` or `heavy` |
| language | `zh` or `en` |

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

```
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

## .env.batch Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| MAIC_API_BASE | (required) | Backend API URL |
| MAIC_EMAIL | (required) | Account email |
| MAIC_PASSWORD | (required) | Account password |
| MAIC_MODEL | glm-4.7 | Default AI model |
| MAIC_LANGUAGE | zh | Output language (zh/en) |
| MAIC_GENERATION_MODE | heavy | PDF generation mode |
| MAIC_PUBLIC | false | Publish publicly |
| MAIC_OUT_DIR | maic_outputs | Single-course output dir |
| MAIC_BATCH_OUT_DIR | maic_batch_outputs | Batch output dir |
| MAIC_CONTINUE_ON_ERROR | true | Keep going on failure |
| MAIC_TIMEOUT_SECONDS | 1800 | Max wait per task |
| MAIC_PPT_BATCH_SIZE | 5 | Slides per PPT batch |

## Recommended Workflow

1. Start with one concept task to verify the pipeline works end-to-end
2. Add one task per row in the TSV manifest with stable ids
3. Run one model at a time first to avoid API throttling and SQLite contention
4. Keep document.json, website.json, and logs as benchmark artifacts
5. Use MAIC-UI WebEditor or human raters to review generated courses
6. Store published versions separately from raw outputs

## Safety Notes

- Use a dedicated low-privilege test account for collaborators
- Tunnel URLs are temporary access tokens — treat them as secrets
- Batch generation consumes the API key on the host MAIC-UI backend
- For large studies, migrate from SQLite to PostgreSQL and add rate limits
- Do not expose the backend publicly without authentication
