#!/bin/bash
set -e

if python3 -c "import flask, flask_wtf, werkzeug, psycopg2, dotenv, PIL, gunicorn" 2>/dev/null; then
    exit 0
fi

echo "[Setup] Installing Python dependencies..."
if ! command -v pip3 >/dev/null 2>&1; then
    curl -sS --connect-timeout 10 --max-time 30 https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    python3 /tmp/get-pip.py --break-system-packages -q --no-warn-script-location
    rm -f /tmp/get-pip.py
fi

pip3 install --break-system-packages --prefer-binary --no-cache-dir --disable-pip-version-check -q -r requirements.txt
