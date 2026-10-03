FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LEARNING_SKIP_RUNTIME_PIP_INSTALL=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /app/venv \
    && /app/venv/bin/pip install --upgrade pip \
    && /app/venv/bin/pip install -r requirements.txt

COPY . .

RUN /app/venv/bin/python manage.py collectstatic --noinput

CMD ["./scripts/runtime/start_django.sh"]
