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

CONCURRENCY="${CELERY_WORKER_CONCURRENCY:-8}"
QUEUES="${CELERY_QUEUES:-celery}"

exec "${PROJECT_ROOT}/venv/bin/celery" -A learning_django worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency="${CONCURRENCY}" \
  --queues="${QUEUES}"
