#!/bin/bash
set -e

bash ensure_python_deps.sh

PORT=3000 exec python3 app.py --port 3000
