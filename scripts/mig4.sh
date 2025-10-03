#!/usr/bin/env bash
set -euo pipefail

DB_DIR="rigapp/app/data"

migrate_db () {
  db="$1"
  echo "== Migrating $db =="

  # RefuelLog optional fields
  sqlite3 "$db" "ALTER TABLE refuel_logs ADD COLUMN tank_capacity_l REAL;" || true
  sqlite3 "$db" "ALTER TABLE refuel_logs ADD COLUMN target_percent INTEGER;" || true
  sqlite3 "$db" "ALTER TABLE refuel_logs ADD COLUMN est_added_litres REAL;" || true

  # JobTask: Fuel Watch fields
  sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN is_fuel_watch BOOLEAN DEFAULT 0;" || true
  sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN tank_capacity_l REAL;" || true
  sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN start_percent INTEGER;" || true
  sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN critical_percent INTEGER;" || true
  sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN hourly_usage_lph REAL;" || true
  sqlite3 "$db" "ALTER TABLE job_tasks ADD COLUMN started_at DATETIME;" || true
}

# default.db (if present)
if [ -f "$DB_DIR/default.db" ]; then
  migrate_db "$DB_DIR/default.db"
fi

# each rig DB (RC*.db)
for db in "$DB_DIR"/RC*.db; do
  [ -e "$db" ] || continue
  migrate_db "$db"
done

echo "All done."
