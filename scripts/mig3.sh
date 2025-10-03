for db in rigapp/app/data/default.db rigapp/app/data/RC*.db; do
  [ -f "$db" ] || continue
  echo "== $(basename "$db") =="
  # Only run if column exists
  has_col=$(sqlite3 "$db" "SELECT 1 FROM pragma_table_info('job_tasks') WHERE name='is_done' LIMIT 1;")
  if [ -n "$has_col" ]; then
    sqlite3 "$db" "UPDATE job_tasks SET is_done = 0 WHERE is_done IS NULL;"
  fi
done
