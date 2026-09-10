#!/bin/sh
set -eu
python -m migrations upgrade
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
