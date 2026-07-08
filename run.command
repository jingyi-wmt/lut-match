#!/bin/zsh
# LUT Match launcher — double-click to start.
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: creating environment…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

( sleep 1.5 && open "http://127.0.0.1:8765" ) &
exec .venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8765
