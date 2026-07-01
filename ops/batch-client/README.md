# MAIC-UI Batch Client

This folder contains a small Windows batch client for generating MAIC-UI courseware from a CSV file.

It assumes you already have a MAIC-UI backend running locally or on a server. The backend owns the model API key. This client only needs:

- backend API URL
- MAIC-UI account email/password
- CSV rows describing PDFs and lesson requirements

Default backend URL:

```text
http://127.0.0.1:8000/api
```

## Files

- `run-batch-generate.cmd`: double-click entry point
- `batch-generate-courses.ps1`: batch upload/poll/download script
- `courses.csv`: editable CSV template
- `pdfs/`: put your source PDFs here
- `outputs/`: generated result CSVs and HTML downloads, ignored by git

## Quick Use

1. Configure and start the MAIC-UI backend from the repository root:

   ```cmd
   copy .env.example .env
   notepad .env
   npm run install:all
   npm run dev
   ```

2. Put source PDFs into `ops\batch-client\pdfs`.
3. Edit `courses.csv`.
4. Double-click `run-batch-generate.cmd`.
5. Enter your MAIC-UI email and password when prompted.
6. Check results in `outputs`.

## CSV Columns

| Column | Required | Description |
|---|---|---|
| `file` | yes | PDF path. Relative paths are resolved against the CSV location. Recommended: `pdfs\your-file.pdf` |
| `page_range` | no | 1-based PDF pages to slice before upload, e.g. `3-5` or `3-5,8` |
| `lesson_id` | no | Stable lesson id for tracking |
| `source_chapter` | no | Source chapter |
| `source_section` | no | Source section |
| `title` | no | Student-visible title; defaults to file name |
| `subject` | no | Subject, e.g. `数学` or `Physics` |
| `grade_level` | no | Grade level, e.g. `10` |
| `description` | no | General generation instructions |
| `concept_name` | no | Core knowledge point |
| `concept_overview` | no | What the lesson should explain |
| `mastery_points` | no | What learners should be able to do |
| `design_idea` | no | Desired interaction idea |
| `assessment_focus` | no | What the in-page check should assess |
| `tracking_plan` | no | Visible learning evidence to include |
| `ai_model` | no | Backend model override; use `default` or blank for backend default |
| `generation_mode` | no | `fast` or `heavy` |
| `is_public` | no | `true` or `false` |
| `include_prerequisites` | no | `true` or `false` |
| `include_exercises` | no | `true` or `false` |

## Manual Commands

```powershell
cd ops\batch-client
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\batch-generate-courses.ps1 -DownloadHtml
```

Use a different backend:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\batch-generate-courses.ps1 -BaseUrl http://192.168.1.10:8000/api -DownloadHtml
```

Only submit jobs without waiting:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\batch-generate-courses.ps1 -NoWait
```
