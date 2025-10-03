#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="rigapp/app/data"

echo "== Migrating DBs in $DATA_DIR =="

sqlite_has_col() {
  local db="$1"
  local table="$2"
  local col="$3"
  sqlite3 "$db" "PRAGMA table_info($table);" | awk -F'|' '{print $2}' | grep -qx "$col"
}

migrate_db() {
  local db="$1"
  echo "--> $db"

  # job_tasks columns (Fuel Watch / time-driven)
  if ! sqlite_has_col "$db" "job_tasks" "is_fuel_watch"; then
    sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN is_fuel_watch BOOLEAN DEFAULT 0;"
  fi
  if ! sqlite_has_col "$db" "job_tasks" "tank_capacity_l"; then
    sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN tank_capacity_l REAL;"
  fi
  if ! sqlite_has_col "$db" "job_tasks" "start_percent"; then
    sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN start_percent INTEGER;"
  fi
  if ! sqlite_has_col "$db" "job_tasks" "critical_percent"; then
    sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN critical_percent INTEGER;"
  fi
  if ! sqlite_has_col "$db" "job_tasks" "hourly_usage_lph"; then
    sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN hourly_usage_lph REAL;"
  fi
  if ! sqlite_has_col "$db" "job_tasks" "started_at"; then
    sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN started_at DATETIME;"
  fi

  # refuel_logs optional helper fields
  if ! sqlite_has_col "$db" "refuel_logs" "tank_capacity_l"; then
    sqlite3 "$db" "ALTER TABLE refuel_logs ADD COLUMN tank_capacity_l REAL;"
  fi
  if ! sqlite_has_col "$db" "refuel_logs" "target_percent"; then
    sqlite3 "$db" "ALTER TABLE refuel_logs ADD COLUMN target_percent INTEGER;"
  fi
  if ! sqlite_has_col "$db" "refuel_logs" "est_added_litres"; then
    sqlite3 "$db" "ALTER TABLE refuel_logs ADD COLUMN est_added_litres REAL;"
  fi
}

# default.db first (if present)
if [ -f "$DATA_DIR/default.db" ]; then
  migrate_db "$DATA_DIR/default.db"
fi

# all rigs starting with RC...
for db in "$DATA_DIR"/RC*.db; do
  [ -e "$db" ] || continue
  migrate_db "$db"
done

echo "== Done =="
