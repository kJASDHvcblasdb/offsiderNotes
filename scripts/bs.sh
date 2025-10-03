source .venv/bin/activate

# Tests

#PYTHONPATH="$PWD" pytest -q

# Run

[ -f rigapp/__init__.py ] || touch rigapp/__init__.py
[ -f rigapp/app/__init__.py ] || touch rigapp/app/__init__.py

rm rigapp.db

export CREW_PIN=1234
python -m rigapp.app.seed --pin "$CREW_PIN" --tz Australia/Perth --horizon 14 --with-sample

# ls -lh rigapp.db

PYTHONPATH="$PWD" uvicorn rigapp.app.main:app --reload --reload-dir rigapp
