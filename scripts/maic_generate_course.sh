#!/usr/bin/env bash
set -euo pipefail

API_BASE="${MAIC_API_BASE:-http://127.0.0.1:8000/api}"
EMAIL="${MAIC_EMAIL:-}"
PASSWORD="${MAIC_PASSWORD:-}"
OUT_DIR="${MAIC_OUT_DIR:-maic_outputs}"
MODEL="${MAIC_MODEL:-glm-4.7}"
PUBLIC="${MAIC_PUBLIC:-false}"
TIMEOUT_SECONDS="${MAIC_TIMEOUT_SECONDS:-1800}"

usage() {
  cat <<'EOF'
Usage:
  MAIC_API_BASE=http://HOST_IP:8000/api MAIC_EMAIL=user@example.com MAIC_PASSWORD='password' \
    ./scripts/maic_generate_course.sh pdf /path/to/file.pdf "Course title"

  MAIC_API_BASE=http://HOST_IP:8000/api MAIC_EMAIL=user@example.com MAIC_PASSWORD='password' \
    ./scripts/maic_generate_course.sh ppt /path/to/slides.pptx "Course title"

  MAIC_API_BASE=http://HOST_IP:8000/api MAIC_EMAIL=user@example.com MAIC_PASSWORD='password' \
    ./scripts/maic_generate_course.sh concept concept.json

Optional env:
  MAIC_MODEL=glm-4.7
  MAIC_SUBJECT=math
  MAIC_GRADE=10
  MAIC_LANGUAGE=zh
  MAIC_GENERATION_MODE=heavy
  MAIC_PUBLIC=false
  MAIC_OUT_DIR=maic_outputs
  MAIC_PPT_BATCH_SIZE=5

concept.json:
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
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

pick_python() {
  if command -v python >/dev/null 2>&1; then PYTHON=(python)
  elif command -v python3 >/dev/null 2>&1; then PYTHON=(python3)
  elif command -v py >/dev/null 2>&1; then PYTHON=(py -3)
  else
    echo "Missing required command: python/python3/py" >&2
    exit 1
  fi
}

json_get() {
  local path="$1"
  "${PYTHON[@]}" -c '
import json, sys
path = [p for p in sys.argv[1].split(".") if p]
try:
    data = json.load(sys.stdin)
    for p in path:
        data = data[int(p)] if isinstance(data, list) else data.get(p)
    if data is None:
        sys.exit(0)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(data)
except Exception:
    sys.exit(0)
' "$path"
}

json_file_get() {
  local file="$1"
  local path="$2"
  "${PYTHON[@]}" -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
for p in [p for p in sys.argv[2].split(".") if p]:
    data = data[int(p)] if isinstance(data, list) else data.get(p)
if data is None:
    sys.exit(0)
if isinstance(data, list):
    print(",".join(str(x) for x in data))
elif isinstance(data, dict):
    print(json.dumps(data, ensure_ascii=False))
elif isinstance(data, bool):
    print("true" if data else "false")
else:
    print(data)
' "$file" "$path"
}

login() {
  if [[ -z "$EMAIL" || -z "$PASSWORD" ]]; then
    echo "Set MAIC_EMAIL and MAIC_PASSWORD first." >&2
    exit 1
  fi

  local payload response token
  payload="$("${PYTHON[@]}" -c 'import json, sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))' "$EMAIL" "$PASSWORD")"
  response="$(curl -sS -X POST "$API_BASE/auth/login" -H "Content-Type: application/json" -d "$payload")"
  token="$(printf '%s' "$response" | json_get access_token)"

  if [[ -z "$token" ]]; then
    echo "Login failed:" >&2
    echo "$response" >&2
    exit 1
  fi

  TOKEN="$token"
}

poll_until_ready() {
  local kind="$1"
  local doc_id="$2"
  local endpoint
  local started now status_json status progress message

  if [[ "$kind" == "ppt" ]]; then
    endpoint="$API_BASE/ppt/documents/$doc_id/status"
  else
    endpoint="$API_BASE/pdf/documents/$doc_id/processing-status"
  fi

  started="$(date +%s)"
  while true; do
    status_json="$(curl -sS -H "Authorization: Bearer $TOKEN" "$endpoint")"
    status="$(printf '%s' "$status_json" | json_get status)"
    progress="$(printf '%s' "$status_json" | json_get progress)"
    message="$(printf '%s' "$status_json" | json_get message)"
    echo "document=$doc_id status=${status:-unknown} progress=${progress:-?}% ${message:-}"

    case "$status" in
      ready) return 0 ;;
      error|failed)
        echo "$status_json" >&2
        return 1
        ;;
      awaiting_template_selection|uploaded)
        echo "Document needs manual/template configuration. Use MAIC_USE_TEMPLATES=false or configure it in the UI." >&2
        echo "$status_json" >&2
        return 1
        ;;
    esac

    now="$(date +%s)"
    if (( now - started > TIMEOUT_SECONDS )); then
      echo "Timed out after ${TIMEOUT_SECONDS}s" >&2
      return 1
    fi
    sleep 5
  done
}

save_pdf_outputs() {
  local doc_id="$1"
  local out="$OUT_DIR/pdf_$doc_id"
  local website_json
  mkdir -p "$out"
  curl -sS -H "Authorization: Bearer $TOKEN" "$API_BASE/pdf/documents/$doc_id" > "$out/document.json"
  website_json="$(curl -sS -H "Authorization: Bearer $TOKEN" "$API_BASE/pdf/documents/$doc_id/website")"
  printf '%s' "$website_json" > "$out/website.json"
  printf '%s' "$website_json" | json_get html > "$out/website.html"
  echo "Saved: $out"
}

save_ppt_outputs() {
  local doc_id="$1"
  local out="$OUT_DIR/ppt_$doc_id"
  mkdir -p "$out"
  curl -sS -H "Authorization: Bearer $TOKEN" "$API_BASE/ppt/documents/$doc_id" > "$out/document.json"
  curl -sS -H "Authorization: Bearer $TOKEN" "$API_BASE/ppt/documents/$doc_id/interactive-view" > "$out/interactive-view.json"
  "${PYTHON[@]}" -c '
import json, os, sys
out = sys.argv[1]
with open(os.path.join(out, "interactive-view.json"), encoding="utf-8") as f:
    data = json.load(f)
for i, item in enumerate(data.get("items", []), 1):
    html = item.get("html")
    if html:
        item_type = item.get("type", "html")
        slide_number = item.get("slide_number", i)
        name = f"item_{i:03d}_{item_type}_slide_{slide_number}.html"
        with open(os.path.join(out, name), "w", encoding="utf-8") as h:
            h.write(html)
' "$out"
  echo "Saved: $out"
}

upload_pdf() {
  local file="$1"
  local title="${2:-}"
  local base prefs response doc_id
  [[ -f "$file" ]] || { echo "File not found: $file" >&2; exit 1; }
  if [[ -z "$title" ]]; then
    base="$(basename "$file")"
    title="${base%.*}"
  fi

  prefs="$("${PYTHON[@]}" -c 'import json, os
print(json.dumps({
  "grade_level": int(os.environ["MAIC_GRADE"]) if os.environ.get("MAIC_GRADE") else None,
  "interests": [x.strip() for x in os.environ.get("MAIC_INTERESTS", "").split(",") if x.strip()],
  "include_exercises": os.environ.get("MAIC_INCLUDE_EXERCISES", "true").lower() == "true",
  "include_prerequisites": os.environ.get("MAIC_INCLUDE_PREREQUISITES", "true").lower() == "true",
  "language": os.environ.get("MAIC_LANGUAGE", "zh")
}, ensure_ascii=False))')"

  form=(-F "file=@${file}" -F "title=${title}" -F "is_public=${PUBLIC}" -F "generation_mode=${MAIC_GENERATION_MODE:-heavy}" -F "ai_model=${MODEL}" -F "user_preferences=${prefs}")
  [[ -n "${MAIC_SUBJECT:-}" ]] && form+=(-F "subject=${MAIC_SUBJECT}")
  [[ -n "${MAIC_GRADE:-}" ]] && form+=(-F "grade_level=${MAIC_GRADE}")
  [[ -n "${MAIC_DESCRIPTION:-}" ]] && form+=(-F "description=${MAIC_DESCRIPTION}")

  response="$(curl -sS -X POST "$API_BASE/pdf/upload" -H "Authorization: Bearer $TOKEN" "${form[@]}")"
  doc_id="$(printf '%s' "$response" | json_get id)"
  [[ -n "$doc_id" ]] || { echo "$response" >&2; exit 1; }
  poll_until_ready pdf "$doc_id"
  save_pdf_outputs "$doc_id"
}

upload_concept() {
  local config="$1"
  local response doc_id
  [[ -f "$config" ]] || { echo "Concept JSON not found: $config" >&2; exit 1; }

  form=(
    -F "subject=$(json_file_get "$config" subject)"
    -F "concept_name=$(json_file_get "$config" concept_name)"
    -F "concept_overview=$(json_file_get "$config" concept_overview)"
    -F "mastery_points=$(json_file_get "$config" mastery_points)"
    -F "design_idea=$(json_file_get "$config" design_idea)"
    -F "is_public=${PUBLIC}"
    -F "ai_model=${MODEL}"
    -F "language=${MAIC_LANGUAGE:-zh}"
  )

  local grade description interests include_exercises include_prerequisites
  grade="$(json_file_get "$config" grade_level)"
  description="$(json_file_get "$config" description)"
  interests="$(json_file_get "$config" interests)"
  include_exercises="$(json_file_get "$config" include_exercises)"
  include_prerequisites="$(json_file_get "$config" include_prerequisites)"
  [[ -n "$grade" ]] && form+=(-F "grade_level=$grade")
  [[ -n "$description" ]] && form+=(-F "description=$description")
  [[ -n "$interests" ]] && form+=(-F "interests=$interests")
  form+=(-F "include_exercises=${include_exercises:-true}")
  form+=(-F "include_prerequisites=${include_prerequisites:-true}")

  response="$(curl -sS -X POST "$API_BASE/pdf/concept/upload" -H "Authorization: Bearer $TOKEN" "${form[@]}")"
  doc_id="$(printf '%s' "$response" | json_get id)"
  [[ -n "$doc_id" ]] || { echo "$response" >&2; exit 1; }
  poll_until_ready pdf "$doc_id"
  save_pdf_outputs "$doc_id"
}

upload_ppt() {
  local file="$1"
  local title="${2:-}"
  local base ext file_type response doc_id config_response
  [[ -f "$file" ]] || { echo "File not found: $file" >&2; exit 1; }
  if [[ -z "$title" ]]; then
    base="$(basename "$file")"
    title="${base%.*}"
  fi
  ext="${file##*.}"
  ext="${ext,,}"
  [[ "$ext" == "pptx" ]] && file_type="pptx" || file_type="pdf"

  form=(-F "file=@${file}" -F "title=${title}" -F "file_type=${file_type}" -F "is_public=${PUBLIC}" -F "auto_process=false" -F "ai_model=${MODEL}")
  [[ -n "${MAIC_SUBJECT:-}" ]] && form+=(-F "subject=${MAIC_SUBJECT}")
  [[ -n "${MAIC_GRADE:-}" ]] && form+=(-F "grade_level=${MAIC_GRADE}")
  [[ -n "${MAIC_DESCRIPTION:-}" ]] && form+=(-F "description=${MAIC_DESCRIPTION}")

  response="$(curl -sS -X POST "$API_BASE/ppt/upload" -H "Authorization: Bearer $TOKEN" "${form[@]}")"
  doc_id="$(printf '%s' "$response" | json_get id)"
  [[ -n "$doc_id" ]] || { echo "$response" >&2; exit 1; }

  config_response="$(curl -sS -X POST "$API_BASE/ppt/documents/$doc_id/configure" \
    -H "Authorization: Bearer $TOKEN" \
    -F "mode=batch" \
    -F "batch_size=${MAIC_PPT_BATCH_SIZE:-5}" \
    -F "use_templates=${MAIC_USE_TEMPLATES:-false}" \
    -F "ai_model=${MODEL}")"
  echo "$config_response" | json_get status >/dev/null || { echo "$config_response" >&2; exit 1; }

  poll_until_ready ppt "$doc_id"
  save_ppt_outputs "$doc_id"
}

main() {
  need_cmd curl
  pick_python

  local command="${1:-}"
  [[ -n "$command" ]] || { usage; exit 1; }
  shift

  case "$command" in
    -h|--help|help)
      usage
      exit 0
      ;;
  esac

  login
  case "$command" in
    pdf) upload_pdf "${1:-}" "${2:-}" ;;
    ppt) upload_ppt "${1:-}" "${2:-}" ;;
    concept) upload_concept "${1:-}" ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
