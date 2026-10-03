import os
from cryptography.fernet import Fernet

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR


_p121_stress_db_path = os.getenv("P121_STRESS_DB_PATH", ":memory:")


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _p121_stress_db_path,
        "OPTIONS": {"timeout": 60} if _p121_stress_db_path != ":memory:" else {},
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "learning_demo-test-cache",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

MIGRATION_MODULES = {
    "main": None,
    "teacher_portal": None,
    "adaptive_agent": None,
    "application": None,
}

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

STATIC_ROOT = BASE_DIR / ".test_staticfiles"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)

# Unit and frozen eval runs must never spend money or depend on network access.
LEARNING_CONCEPT_REMOTE_EMBEDDINGS_ENABLED = False
LEARNING_AGENT_V2_ENABLED = True
LEARNING_AGENT_CHECKPOINT_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
