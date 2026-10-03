#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

if [ "${LEARNING_SKIP_RUNTIME_PIP_INSTALL:-0}" != "1" ]; then
  "${PROJECT_ROOT}/venv/bin/pip" install -r requirements.txt
fi

if [ "${LEARNING_SKIP_MIGRATIONS:-0}" != "1" ]; then
  "${PROJECT_ROOT}/venv/bin/python" manage.py migrate --noinput
fi

if [ "${DJANGO_RUNSERVER:-0}" = "1" ]; then
  exec "${PROJECT_ROOT}/venv/bin/python" manage.py runserver "${1:-127.0.0.1:8002}"
fi

BIND_ADDRESS="${1:-${WEB_BIND:-0.0.0.0:${PORT:-8002}}}"
WORKERS="${GUNICORN_WORKERS:-4}"
THREADS="${GUNICORN_THREADS:-8}"
TIMEOUT="${GUNICORN_TIMEOUT:-90}"

exec "${PROJECT_ROOT}/venv/bin/gunicorn" learning_django.wsgi:application \
  --bind "${BIND_ADDRESS}" \
  --workers "${WORKERS}" \
  --threads "${THREADS}" \
  --timeout "${TIMEOUT}" \
  --access-logfile - \
  --error-logfile -
