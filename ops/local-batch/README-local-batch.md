# MAIC-UI / OpenMAIC Local Batch Helpers

This folder is for Windows local batch generation and local service orchestration.

It is intended for developers or teachers who clone the repository, configure their own model API keys, and generate courseware on their own machine.

## What This Adds

`ops/local-batch` adds a local workflow around the existing backend:

- local service startup/shutdown scripts for MAIC-UI backend, MAIC-UI frontend, and optional OpenMAIC
- MAIC-UI batch generation against `http://127.0.0.1:8000/api`
- OpenMAIC batch generation against `http://localhost:3001`
- CSV-driven sequential processing for stability
- PDF `page_range` slicing before upload, so each row can target only a small part of a source PDF
- output folders for generated HTML, result CSVs, classroom JSON, and logs
- learning-evidence prompt fields such as `assessment_focus` and `tracking_plan`

Model provider credentials are **not** stored here. Put your own API keys in the backend `.env` file.

## 1. Configure Your API Key

From the repository root:

```cmd
copy .env.example .env
notepad .env
```

At minimum, set your OpenAI-compatible endpoint key:

```env
TRANSFER_API_KEY=sk-your-key
TRANSFER_BASE_URL=https://api.siliconflow.cn/v1
TRANSFER_MODEL=glm-4.7
```

## 2. Install Dependencies

From the repository root:

```cmd
npm run install:all
```

The PDF page-slicing scripts require Python package `PyPDF2`, which is already listed in `backend/requirements.txt`.

## 3. Start Local Services

Double-click:

```text
ops\local-batch\start-local-services.cmd
```

By default it starts MAIC-UI. If an `OpenMAIC` folder is found next to this repository, it also starts OpenMAIC; otherwise it prints a warning and skips OpenMAIC.

Services:

```text
MAIC-UI frontend: http://localhost:3000
MAIC-UI backend:  http://127.0.0.1:8000
OpenMAIC:         http://localhost:3001
```

Logs are written to ignored folder:

```text
ops\local-batch\logs\
```

If you only need MAIC-UI batch generation, OpenMAIC can be skipped by running PowerShell manually:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ops\local-batch\start-local-services.ps1 -SkipOpenMaic
```

If OpenMAIC is not next to this repository, pass explicit roots:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ops\local-batch\start-local-services.ps1 -MaicUiRoot C:\path\to\MAIC-UI -OpenMaicRoot C:\path\to\OpenMAIC
```

## 4. Run MAIC-UI Local Batch

Prepare a CSV with columns matching `ops\batch-client\courses.csv`:

```text
file,page_range,lesson_id,source_chapter,source_section,title,subject,grade_level,description,concept_name,concept_overview,mastery_points,design_idea,assessment_focus,tracking_plan,ai_model,generation_mode,is_public,include_prerequisites,include_exercises
```

Important fields:

| Column | Meaning |
|---|---|
| `file` | PDF path; relative paths are resolved against the CSV location |
| `page_range` | Optional 1-based PDF pages, e.g. `3-5` or `3-5,8`; the script slices before upload |
| `title` | Student-visible course title |
| `description` | General generation requirement |
| `concept_name` | Core knowledge point |
| `concept_overview` | What this lesson should explain |
| `mastery_points` | What students should be able to do |
| `design_idea` | Desired interaction design |
| `assessment_focus` | What the formative check should assess |
| `tracking_plan` | Learning evidence to make visible inside the page |
| `ai_model` | Backend model override, or `default`/blank |
| `generation_mode` | `fast` or `heavy` |

Then double-click:

```text
ops\local-batch\run-maicui-local-batch.cmd
```

The script prompts for a MAIC-UI account email and password, uploads each row sequentially, polls status, and optionally downloads generated HTML.

Results are written to ignored folder:

```text
ops\local-batch\outputs\maicui\
```

## 5. Run OpenMAIC Local Batch

OpenMAIC rows use a different CSV shape:

```text
file,page_range,title,requirement,pdf_provider_id,enable_web_search,enable_image_generation,enable_video_generation,enable_tts,agent_mode
```

Double-click:

```text
ops\local-batch\run-openmaic-local-batch.cmd
```

The script can:

- slice PDF pages using `page_range`
- call OpenMAIC `/api/parse-pdf`
- submit `/api/generate-classroom`
- poll until classroom generation finishes
- save result CSV and optional classroom JSON

Results are written to ignored folder:

```text
ops\local-batch\outputs\openmaic\
```

## 6. Stop Local Services

Double-click:

```text
ops\local-batch\stop-local-services.cmd
```

It stops only processes recorded in `local-service-pids.json`.

## Notes

- Generated outputs, logs, backups, PID files, and PDF slices are intentionally ignored by git.
- Start with one row before running a large CSV.
- SQLite is fine for local experiments, but large batches are safer with PostgreSQL.
- If generation frequently fails HTML quality validation, fix the prompt requirements first; blind retries are expensive on slow models.
