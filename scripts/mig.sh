#!/usr/bin/env bash
set -euo pipefail

# Directory containing your SQLite DBs
DB_DIR="rigapp/app/data"

# Make globbing nice (ignore patterns that don't match)
shopt -s nullglob

# Helper: add a column if it doesn't exist
add_col_if_missing() {
  local db="$1"
  local tbl="$2"
  local col="$3"
  local decl="$4"

  local present
  present=$(sqlite3 "$db" "SELECT 1 FROM pragma_table_info('$tbl') WHERE name='$col' LIMIT 1;")
  if [[ -z "$present" ]]; then
    echo "  -> $tbl: adding column $col"
    sqlite3 "$db" "ALTER TABLE $tbl ADD COLUMN $decl;"
  fi
}

# Helper: ensure a table exists with a full definition (CREATE TABLE IF NOT EXISTS is idempotent)
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

# Run migrations for a single DB file
migrate_db() {
  local db="$1"
  echo "== $(basename "$db") =="

  # 1) job_tasks table + columns
  ensure_job_tasks_table "$db"
  add_col_if_missing "$db" job_tasks notes "notes TEXT"
  add_col_if_missing "$db" job_tasks priority "priority INTEGER DEFAULT 2"
  add_col_if_missing "$db" job_tasks is_closed "is_closed BOOLEAN DEFAULT 0"
  add_col_if_missing "$db" job_tasks created_at "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"

  # 2) handover_notes.is_closed (for closing notes)
  add_col_if_missing "$db" handover_notes is_closed "is_closed BOOLEAN DEFAULT 0"

  # 3) equipment_faults.priority (we use priority there too)
  add_col_if_missing "$db" equipment_faults priority "priority INTEGER DEFAULT 2"
}

main() {
  if [[ ! -d "$DB_DIR" ]]; then
    echo "Data directory not found: $DB_DIR" >&2
    exit 1
  fi

  # Collect DBs: default.db + RC*.db (and any other .db in the folder)
  dbs=("$DB_DIR"/default.db "$DB_DIR"/RC*.db)
  # Also include any other .db files if you want:
  # dbs+=("$DB_DIR"/*.db)

  # Filter out non-existent globs quietly
  for db in "${dbs[@]}"; do
    if [[ -f "$db" ]]; then
      migrate_db "$db"
    fi
  done

  echo "Done."
}

main "$@"
