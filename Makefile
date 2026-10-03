PYTHON ?= python3

.PHONY: check test migrate run worker

check:
	$(PYTHON) scripts/quality/check_tracked_secrets.py
	$(PYTHON) -m ruff check learning_apps learning_django scripts tests manage.py
	REDIS_URL= LEARNING_USE_CELERY=false $(PYTHON) -m pytest -q
	DJANGO_SETTINGS_MODULE=learning_django.settings_test $(PYTHON) manage.py check
	DJANGO_SETTINGS_MODULE=learning_django.settings_test $(PYTHON) manage.py makemigrations --check --dry-run

test:
	REDIS_URL= LEARNING_USE_CELERY=false $(PYTHON) -m pytest -q

migrate:
	$(PYTHON) manage.py migrate

run:
	$(PYTHON) manage.py runserver

worker:
	$(PYTHON) -m celery -A learning_django worker --loglevel=INFO
