"""WSGI config for the Learning Workflow Demo Django project."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_django.settings")

application = get_wsgi_application()
