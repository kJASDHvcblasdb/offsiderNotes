#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

DB_DIR="rigapp/app/data"

add_col_if_missing() {
  local db="$1" tbl="$2" col="$3" decl="$4"
  local present
  present=$(sqlite3 "$db" "SELECT 1 FROM pragma_table_info('$tbl') WHERE name='$col' LIMIT 1;")
  if [[ -z "$present" ]]; then
    echo "  -> $tbl: adding column $col"
    sqlite3 "$db" "ALTER TABLE $tbl ADD COLUMN $decl;"
  fi
}

ensure_job_tasks_table() {
  local db="$1"
  sqlite3 "$db" "
    CREATE TABLE IF NOT EXISTS job_tasks (
      id INTEGER PRIMARY KEY,
      title VARCHAR(200) NOT NULL,
      notes TEXT,
      priority INTEGER DEFAULT 2,
      is_closed BOOLEAN DEFAULT 0,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
  "
}

sync_is_done_is_closed() {
  local db="$1"
  # If both columns exist, keep them in sync.
  local has_closed has_done
  has_closed=$(sqlite3 "$db" "SELECT 1 FROM pragma_table_info('job_tasks') WHERE name='is_closed' LIMIT 1;")
  has_done=$(sqlite3 "$db" "SELECT 1 FROM pragma_table_info('job_tasks') WHERE name='is_done' LIMIT 1;")
  if [[ -n "$has_closed" && -n "$has_done" ]]; then
    # Fill NULLs and resolve mismatches in both directions.
    sqlite3 "$db" "
      UPDATE job_tasks SET is_done   = COALESCE(is_done,   0);
      UPDATE job_tasks SET is_closed = COALESCE(is_closed, 0);
      UPDATE job_tasks SET is_done   = is_closed WHERE is_done   != is_closed;
      UPDATE job_tasks SET is_closed = is_done   WHERE is_closed != is_done;
    "
  fi
}

migrate_db() {
  local db="$1"
  echo "== $(basename "$db") =="

  # 1) job_tasks base
  ensure_job_tasks_table "$db"
  add_col_if_missing "$db" job_tasks notes      "notes TEXT"
  add_col_if_missing "$db" job_tasks priority   "priority INTEGER DEFAULT 2"
  add_col_if_missing "$db" job_tasks is_closed  "is_closed BOOLEAN DEFAULT 0"
  # Add is_done for legacy DBs that used that name (NOT NULL with default so inserts don’t fail)
  add_col_if_missing "$db" job_tasks is_done    "is_done BOOLEAN NOT NULL DEFAULT 0"

  # Keep both columns in sync if both exist
  sync_is_done_is_closed "$db"

  # 2) handover_notes.is_closed
  add_col_if_missing "$db" handover_notes is_closed "is_closed BOOLEAN DEFAULT 0"

  # 3) equipment_faults.priority (used in jobs/critical view)
  add_col_if_missing "$db" equipment_faults priority "priority INTEGER DEFAULT 2"
}

main() {
  if [[ ! -d "$DB_DIR" ]]; then
    echo "Data directory not found: $DB_DIR" >&2
    exit 1
  fi

  dbs=("$DB_DIR"/default.db "$DB_DIR"/RC*.db)
  for db in "${dbs[@]}"; do
    [[ -f "$db" ]] && migrate_db "$db"
  done

  echo "Done."
}

main "$@"
