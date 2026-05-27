#!/usr/bin/env bash
set -euo pipefail

case "${BASH_SOURCE[0]}" in
  */*) SCRIPT_DIR_RAW="${BASH_SOURCE[0]%/*}" ;;
  *) SCRIPT_DIR_RAW="." ;;
esac
SCRIPT_DIR="$(cd "$SCRIPT_DIR_RAW" && pwd)"
GENERATOR="${SCRIPT_DIR}/maic_generate_course.sh"

MANIFEST="${1:-}"
BATCH_OUT_DIR="${MAIC_BATCH_OUT_DIR:-maic_batch_outputs}"
LOG_DIR="${MAIC_BATCH_LOG_DIR:-${BATCH_OUT_DIR}/logs}"
CONTINUE_ON_ERROR="${MAIC_CONTINUE_ON_ERROR:-true}"

usage() {
  cat <<'EOF'
Usage:
  MAIC_API_BASE=http://HOST_IP:8000/api MAIC_EMAIL=user@example.com MAIC_PASSWORD='password' \
    ./scripts/maic_batch_generate.sh examples/batch_courses.tsv

Manifest format: tab-separated values with this header:
  id type input title subject grade model public mode language

Columns:
  id        Stable local task id, for logs and output folders.
  type      pdf, ppt, or concept.
  input     PDF/PPTX/PDF-slides file path, or concept JSON path.
  title     Course title. Ignored for concept; concept_name is read from JSON.
  subject   Optional subject, e.g. math.
  grade     Optional grade level, e.g. 10.
  model     Optional model, e.g. glm-4.7.
  public    true or false.
  mode      PDF generation mode: fast or heavy. PPT always uses batch configure.
  language  zh or en.

Blank lines and lines beginning with # are ignored.
EOF
}

trim_cr() {
  printf '%s' "$1" | tr -d '\r'
}

is_abs_path() {
  case "$1" in
    /*|[A-Za-z]:/*|[A-Za-z]:\\*) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_input_path() {
  local manifest_dir="$1"
  local input="$2"
  if is_abs_path "$input"; then
    printf '%s' "$input"
  else
    printf '%s/%s' "$manifest_dir" "$input"
  fi
}

run_one() {
  local id="$1"
  local type="$2"
  local input="$3"
  local title="$4"
  local subject="$5"
  local grade="$6"
  local model="$7"
  local public="$8"
  local mode="$9"
  local language="${10}"

  local task_out="${BATCH_OUT_DIR}/${id}"
  local log_file="${LOG_DIR}/${id}.log"
  mkdir -p "$task_out" "$LOG_DIR"

  echo "[$(date -Is)] START id=${id} type=${type} input=${input}" | tee "$log_file"

  (
    export MAIC_OUT_DIR="$task_out"
    export MAIC_SUBJECT="$subject"
    export MAIC_GRADE="$grade"
    export MAIC_MODEL="${model:-${MAIC_MODEL:-glm-4.7}}"
    export MAIC_PUBLIC="${public:-${MAIC_PUBLIC:-false}}"
    export MAIC_GENERATION_MODE="${mode:-${MAIC_GENERATION_MODE:-heavy}}"
    export MAIC_LANGUAGE="${language:-${MAIC_LANGUAGE:-zh}}"

    case "$type" in
      pdf|ppt)
        "$GENERATOR" "$type" "$input" "$title"
        ;;
      concept)
        "$GENERATOR" concept "$input"
        ;;
      *)
        echo "Unsupported type: $type" >&2
        exit 2
        ;;
    esac
  ) 2>&1 | tee -a "$log_file"

  local rc="${PIPESTATUS[0]}"
  if [[ "$rc" -eq 0 ]]; then
    echo "[$(date -Is)] OK id=${id}" | tee -a "$log_file"
  else
    echo "[$(date -Is)] FAILED id=${id} exit=${rc}" | tee -a "$log_file"
  fi
  return "$rc"
}

main() {
  if [[ -z "$MANIFEST" || "$MANIFEST" == "-h" || "$MANIFEST" == "--help" ]]; then
    usage
    exit 0
  fi
  if [[ ! -f "$MANIFEST" ]]; then
    echo "Manifest not found: $MANIFEST" >&2
    exit 1
  fi
  if [[ ! -x "$GENERATOR" && ! -f "$GENERATOR" ]]; then
    echo "Generator script not found: $GENERATOR" >&2
    exit 1
  fi

  local manifest_dir_raw manifest_dir
  case "$MANIFEST" in
    */*) manifest_dir_raw="${MANIFEST%/*}" ;;
    *) manifest_dir_raw="." ;;
  esac
  manifest_dir="$(cd "$manifest_dir_raw" && pwd)"
  mkdir -p "$BATCH_OUT_DIR" "$LOG_DIR"

  local total=0
  local ok=0
  local failed=0
  local line_no=0

  while IFS=$'\t' read -r id type input title subject grade model public mode language extra || [[ -n "${id:-}" ]]; do
    line_no=$((line_no + 1))
    id="$(trim_cr "${id:-}")"
    type="$(trim_cr "${type:-}")"
    input="$(trim_cr "${input:-}")"
    title="$(trim_cr "${title:-}")"
    subject="$(trim_cr "${subject:-}")"
    grade="$(trim_cr "${grade:-}")"
    model="$(trim_cr "${model:-}")"
    public="$(trim_cr "${public:-}")"
    mode="$(trim_cr "${mode:-}")"
    language="$(trim_cr "${language:-}")"

    [[ -z "$id" ]] && continue
    [[ "$id" == \#* ]] && continue
    [[ "$id" == "id" && "$type" == "type" ]] && continue

    if [[ -z "$type" || -z "$input" ]]; then
      echo "Skipping line ${line_no}: type and input are required" >&2
      continue
    fi

    local resolved_input
    resolved_input="$(resolve_input_path "$manifest_dir" "$input")"

    total=$((total + 1))
    if run_one "$id" "$type" "$resolved_input" "$title" "$subject" "$grade" "$model" "$public" "$mode" "$language"; then
      ok=$((ok + 1))
    else
      failed=$((failed + 1))
      if [[ "$CONTINUE_ON_ERROR" != "true" ]]; then
        break
      fi
    fi
  done < "$MANIFEST"

  cat > "${BATCH_OUT_DIR}/summary.txt" <<EOF
completed_at=$(date -Is)
manifest=${MANIFEST}
total=${total}
ok=${ok}
failed=${failed}
outputs=${BATCH_OUT_DIR}
logs=${LOG_DIR}
EOF

  echo "Batch complete: total=${total} ok=${ok} failed=${failed}"
  echo "Summary: ${BATCH_OUT_DIR}/summary.txt"

  [[ "$failed" -eq 0 ]]
}

main "$@"
