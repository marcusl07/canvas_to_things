#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/archive_task.sh <TASK_ID>

Examples:
  scripts/archive_task.sh T40
  scripts/archive_task.sh T94-a
  scripts/archive_task.sh H2

Moves one task/bug section from tasks-active.txt to tasks-archive.txt.
For H-items, the archive heading is marked with a checkmark if needed.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 1
fi

task_id="$1"
active_file="tasks-active.txt"
archive_file="tasks-archive.txt"

if [[ ! -f "$active_file" ]]; then
  echo "Active task file not found: $active_file" >&2
  exit 1
fi

if [[ ! -f "$archive_file" ]]; then
  echo "Archive task file not found: $archive_file" >&2
  exit 1
fi

case "$task_id" in
  T[0-9]*)
    task_id_regex='T[0-9]+(-[a-z]+)?'
    start_regex="^\\*\\*$task_id[[:space:]]+—"
    next_regex="^\\*\\*$task_id_regex[[:space:]]+—"
    ;;
  H[0-9]*)
    start_regex="^### (✅ )?$task_id[[:space:]]+—"
    next_regex='^### (✅ )?H[0-9]+[[:space:]]+—'
    ;;
  *)
    echo "Invalid task id '$task_id'. Use formats like T40, T94-a, or H2." >&2
    exit 1
    ;;
esac

if rg -q "$start_regex" "$archive_file"; then
  echo "Refusing to archive '$task_id': appears to already exist in $archive_file" >&2
  exit 1
fi

tmp_active="$(mktemp)"
tmp_block="$(mktemp)"
tmp_block_final="$(mktemp)"
cleanup() {
  rm -f "$tmp_active" "$tmp_block" "$tmp_block_final"
}
trap cleanup EXIT

awk \
  -v start="$start_regex" \
  -v next_header="$next_regex" \
  -v active_out="$tmp_active" \
  -v block_out="$tmp_block" '
BEGIN {
  in_block = 0
  found = 0
}
{
  if (!in_block) {
    if ($0 ~ start) {
      in_block = 1
      found = 1
      print $0 >> block_out
    } else {
      print $0 >> active_out
    }
    next
  }

  if ($0 ~ next_header) {
    in_block = 0
    print $0 >> active_out
    next
  }

  print $0 >> block_out
}
END {
  if (!found) {
    exit 2
  }
}
' "$active_file"
awk_status=$?
if [[ $awk_status -eq 2 ]]; then
  echo "Task '$task_id' was not found in $active_file" >&2
  exit 1
fi
if [[ $awk_status -ne 0 ]]; then
  exit "$awk_status"
fi

cp "$tmp_block" "$tmp_block_final"

if [[ "$task_id" == H* ]]; then
  first_line="$(head -n 1 "$tmp_block_final")"
  if [[ "$first_line" =~ ^###\ ✅\  ]]; then
    :
  else
    sed -E '1 s/^### /### ✅ /' "$tmp_block_final" > "${tmp_block_final}.tmp"
    mv "${tmp_block_final}.tmp" "$tmp_block_final"
  fi
fi

mv "$tmp_active" "$active_file"

{
  printf '\n'
  cat "$tmp_block_final"
  printf '\n'
} >> "$archive_file"

echo "Archived $task_id from $active_file to $archive_file"
