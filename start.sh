#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

/usr/bin/python3 -c "import flask" 2>/dev/null || /usr/bin/python3 -m pip install --user -q "flask>=3.0.0"

exec /usr/bin/python3 app.py
