#!/usr/bin/env sh
set -e

# Wait for the database to accept connections.
python - <<'PY'
import os, time, sqlalchemy
url = os.environ.get("DATABASE_URL", "")
if url.startswith("postgresql"):
    for _ in range(60):
        try:
            sqlalchemy.create_engine(url).connect().close()
            break
        except Exception as ex:
            print("waiting for db:", ex, flush=True)
            time.sleep(2)
    else:
        raise SystemExit("database not reachable")
PY

# Import CPHI data once, only if the ranking table is empty (backend service only).
if [ "${AUTO_IMPORT:-0}" = "1" ]; then
  NEED=$(python - <<'PY'
import os, sqlalchemy
from sqlalchemy import text
need = True
try:
    e = sqlalchemy.create_engine(os.environ["DATABASE_URL"])
    with e.connect() as c:
        need = not c.execute(text("select count(*) from ranking")).scalar()
except Exception:
    need = True
print("1" if need else "0")
PY
)
  if [ "$NEED" = "1" ]; then
    echo "Importing CPHI data..."
    python import_data.py || echo "WARNING: import failed (is CPHI_DATA_DIR mounted?)"
  else
    echo "Data already present, skipping import."
  fi
fi

exec "$@"
