#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x "venv/bin/python" ]]; then
  PYTHON="venv/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  echo "Python virtual environment not found. Create venv and install requirements.txt first." >&2
  exit 1
fi

"$PYTHON" pipeline/run.py --refresh
"$PYTHON" -m streamlit run app/dashboard.py --server.headless=true --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false
