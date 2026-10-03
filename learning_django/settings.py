"""Django settings for the Learning Workflow Demo migration."""
from pathlib import Path
import os
import sys
from urllib.parse import parse_qs, unquote, urlparse
from dotenv import load_dotenv
from django.contrib.messages import constants as message_constants
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent
RUNNING_TESTS = "test" in sys.argv

# Load environment variables from the existing .env file if present.
env_file = BASE_DIR / ".env"
if env_file.exists():
    load_dotenv(env_file)


def _parse_int(value: str, fallback: int) -> int:
    """Internal helper to parse int."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback


def _parse_bool(value: str | bool | None, fallback: bool) -> bool:
    """Internal helper to parse bool-like environment values."""
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _database_from_url(raw_url: str) -> dict:
    """Parse DATABASE_URL into a Django DATABASES['default'] dict."""
    parsed = urlparse(raw_url)
    scheme = (parsed.scheme or "").strip().lower()
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]

    if scheme in {"postgres", "postgresql"}:
        engine = "django.db.backends.postgresql"
    elif scheme in {"mysql"}:
        engine = "django.db.backends.mysql"
    elif scheme in {"sqlite", "sqlite3"}:
        engine = "django.db.backends.sqlite3"
    else:
        raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: '{parsed.scheme}'")

    if engine == "django.db.backends.sqlite3":
        db_path = unquote(parsed.path or "")
        if db_path.startswith("//"):
            db_path = db_path[1:]
        elif db_path.startswith("/"):
            db_path = db_path
        if not db_path:
            db_path = str(BASE_DIR / "db.sqlite3")
        return {"ENGINE": engine, "NAME": db_path}

    query = parse_qs(parsed.query or "", keep_blank_values=False)
    db: dict = {
        "ENGINE": engine,
        "NAME": unquote((parsed.path or "").lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
    }

    options: dict = {}
    sslmode = (query.get("sslmode") or [None])[0]
    if sslmode:
        options["sslmode"] = sslmode
    charset = (query.get("charset") or [None])[0]
    if charset:
        options["charset"] = charset
    connect_timeout = (query.get("connect_timeout") or [None])[0]
    if connect_timeout:
        options["connect_timeout"] = _parse_int(connect_timeout, 10)
    if options:
        db["OPTIONS"] = options
    return db


allowed_hosts = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts.split(",") if host.strip()]
_LOCAL_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"}
_LOCAL_ONLY_DEPLOYMENT = bool(ALLOWED_HOSTS) and all(host in _LOCAL_ALLOWED_HOSTS for host in ALLOWED_HOSTS)

DEBUG = _parse_bool(os.getenv("DJANGO_DEBUG"), _LOCAL_ONLY_DEPLOYMENT)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = get_random_secret_key()
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is not enabled.")

# Third-party provider configuration has one process entrypoint: Django
# settings. Application and product services must not read the environment
# directly or cache these values at module import time.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-2025-04-14").strip()
LEARNING_SELF_ASSESSMENT_V2_ENABLED = _parse_bool(
    os.getenv("LEARNING_SELF_ASSESSMENT_V2_ENABLED"),
    True,
)
LEARNING_SELF_ASSESSMENT_MODEL = os.getenv(
    "LEARNING_SELF_ASSESSMENT_MODEL",
    "gpt-5.6-sol",
).strip()
LEARNING_SELF_ASSESSMENT_REASONING_EFFORT = os.getenv(
    "LEARNING_SELF_ASSESSMENT_REASONING_EFFORT",
    "medium",
).strip()
LEARNING_SELF_ASSESSMENT_TIMEOUT_SECONDS = _parse_int(
    os.getenv("LEARNING_SELF_ASSESSMENT_TIMEOUT_SECONDS"),
    120,
)
LEARNING_SELF_ASSESSMENT_BLUEPRINT_MODEL = os.getenv(
    "LEARNING_SELF_ASSESSMENT_BLUEPRINT_MODEL",
    "gpt-5.6-sol",
).strip()
LEARNING_SELF_ASSESSMENT_BLUEPRINT_REASONING_EFFORT = os.getenv(
    "LEARNING_SELF_ASSESSMENT_BLUEPRINT_REASONING_EFFORT",
    "medium",
).strip()
LEARNING_SELF_ASSESSMENT_TEMPLATE_MODEL = os.getenv(
    "LEARNING_SELF_ASSESSMENT_TEMPLATE_MODEL",
    "gpt-5.6-terra",
).strip()
LEARNING_ADAPTIVE_PROBE_MODEL = os.getenv(
    "LEARNING_ADAPTIVE_PROBE_MODEL",
    "gpt-5.6-terra",
).strip()
LEARNING_ADAPTIVE_GRADER_MODEL = os.getenv(
    "LEARNING_ADAPTIVE_GRADER_MODEL",
    "gpt-5.6-sol",
).strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

_SECURE_DEPLOYMENT_DEFAULT = (not DEBUG) and (not _LOCAL_ONLY_DEPLOYMENT)

csrf_trusted = os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [host.strip() for host in csrf_trusted.split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "learning_apps.persistence.apps.PersistenceConfig",
    "learning_apps.application.apps.ApplicationConfig",
    "learning_apps.accounts",
    "learning_apps.learning_goal.apps.LearningGoalConfig",
    "learning_apps.knowledge.apps.KnowledgeConfig",
    "learning_apps.self_assessment.apps.SelfAssessmentConfig",
    "learning_apps.chat.apps.ChatConfig",
    "learning_apps.adaptive_learning.apps.AdaptiveLearningConfig",
    "learning_apps.web.apps.WebConfig",
    "learning_apps.teacher_portal",
    "learning_apps.adaptive_agent",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "learning_apps.infrastructure.middleware.RequestTraceMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "learning_apps.accounts.auth_backends.UserProfileBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LOGIN_RATE_LIMIT_ATTEMPTS = _parse_int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS"), 5)
LOGIN_RATE_LIMIT_WINDOW_SECONDS = _parse_int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS"), 15 * 60)
LOGIN_RATE_LIMIT_LOCKOUT_SECONDS = _parse_int(os.getenv("LOGIN_RATE_LIMIT_LOCKOUT_SECONDS"), 15 * 60)

REDIS_URL = os.getenv("REDIS_URL", "").strip()
if not DEBUG and not REDIS_URL and not _parse_bool(os.getenv("ALLOW_UNSAFE_LOCAL_CACHE"), False):
    raise ImproperlyConfigured("REDIS_URL must be set in production for shared cache, jobs, and rate limits.")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
            "KEY_PREFIX": os.getenv("DJANGO_CACHE_KEY_PREFIX", "learning_demo"),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": str(BASE_DIR / ".django_cache"),
            "TIMEOUT": _parse_int(os.getenv("DJANGO_CACHE_TIMEOUT_SECONDS"), 1800),
        }
    }

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db" if REDIS_URL else "django.contrib.sessions.backends.signed_cookies"

LOGIN_URL = "/auth/login"
LOGIN_REDIRECT_URL = "/learning-goal"
LOGOUT_REDIRECT_URL = "/"

ROOT_URLCONF = "learning_django.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates_django"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "learning_apps.teacher_portal.context_processors.class_invitation_notifications",
            ]
        },
    }
]

WSGI_APPLICATION = "learning_django.wsgi.application"
ASGI_APPLICATION = "learning_django.asgi.application"

_database_url = os.getenv("DATABASE_URL", "").strip() or os.getenv("DJANGO_DATABASE_URL", "").strip()
if _database_url:
    _default_database = _database_from_url(_database_url)
else:
    _default_database = {
        "ENGINE": os.getenv("DJANGO_DB_ENGINE", "django.db.backends.mysql"),
        "NAME": os.getenv("DB_NAME", "adaptive_assistant"),
        "USER": os.getenv("DB_USER", "adaptive_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": str(_parse_int(os.getenv("DB_PORT"), 3306)),
        "OPTIONS": {"charset": "utf8mb4"},
    }
    if _default_database["ENGINE"] == "django.db.backends.sqlite3":
        _default_database = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }

_using_sqlite = _default_database["ENGINE"] == "django.db.backends.sqlite3"
if _using_sqlite:
    _sqlite_options = dict(_default_database.get("OPTIONS") or {})
    _sqlite_options.setdefault(
        "timeout",
        _parse_int(os.getenv("SQLITE_TIMEOUT_SECONDS"), 60),
    )
    _default_database["OPTIONS"] = _sqlite_options

_db_conn_max_age = _parse_int(
    os.getenv("DB_CONN_MAX_AGE"),
    0 if _using_sqlite else 60,
)
if _parse_bool(os.getenv("DB_USE_PGBOUNCER_TRANSACTION_POOLING"), False):
    # PgBouncer transaction pooling owns server connection reuse.
    _db_conn_max_age = 0
    _default_database["DISABLE_SERVER_SIDE_CURSORS"] = True

_default_database["CONN_MAX_AGE"] = _db_conn_max_age
_default_database["CONN_HEALTH_CHECKS"] = True

DATABASES = {"default": _default_database}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL") or REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND") or REDIS_URL or "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = _parse_bool(os.getenv("CELERY_TASK_ALWAYS_EAGER"), False)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = _parse_int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER"), 1)
CELERY_TASK_TIME_LIMIT = _parse_int(os.getenv("CELERY_TASK_TIME_LIMIT_SECONDS"), 180)
CELERY_TASK_SOFT_TIME_LIMIT = _parse_int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS"), 150)
CELERY_BEAT_SCHEDULE = {
    "adaptive-agent-run-expiry": {
        "task": "adaptive_agent.expire_stale_runs",
        "schedule": float(os.getenv("LEARNING_AGENT_EXPIRY_SCAN_INTERVAL_SECONDS", "300")),
        "kwargs": {"limit": _parse_int(os.getenv("LEARNING_AGENT_EXPIRY_SCAN_LIMIT"), 200)},
    },
    "adaptive-agent-run-recovery": {
        "task": "adaptive_agent.recover_stale_runs",
        "schedule": float(os.getenv("LEARNING_AGENT_RECOVERY_SCAN_INTERVAL_SECONDS", "60")),
        "kwargs": {"limit": _parse_int(os.getenv("LEARNING_AGENT_RECOVERY_SCAN_LIMIT"), 100)},
    },
    "adaptive-review-scan": {
        "task": "adaptive_learning.scan_reviews",
        "schedule": float(os.getenv("LEARNING_REVIEW_SCAN_INTERVAL_SECONDS", "300")),
        "kwargs": {"limit": _parse_int(os.getenv("LEARNING_REVIEW_SCAN_LIMIT"), 100)},
    },
    "conversation-cleanup-reconcile": {
        "task": "chat.reconcile_conversation_cleanup",
        "schedule": float(os.getenv("LEARNING_CONVERSATION_CLEANUP_INTERVAL_SECONDS", "300")),
        "kwargs": {
            "cleanup_limit": _parse_int(os.getenv("LEARNING_CONVERSATION_CLEANUP_LIMIT"), 100),
            "retention_limit": _parse_int(os.getenv("LEARNING_CONVERSATION_RETENTION_CLEANUP_LIMIT"), 500),
        },
    },
    "learner-memory-expiry": {
        "task": "chat.expire_learner_memories",
        "schedule": float(os.getenv("LEARNING_LEARNER_MEMORY_EXPIRY_INTERVAL_SECONDS", "300")),
        "kwargs": {"limit": _parse_int(os.getenv("LEARNING_LEARNER_MEMORY_EXPIRY_LIMIT"), 500)},
    },
}
LEARNING_USE_CELERY = _parse_bool(os.getenv("LEARNING_USE_CELERY"), bool(REDIS_URL))
LEARNING_CELERY_WORKER_HEALTHCHECK_TIMEOUT_SECONDS = max(
    0.1,
    float(os.getenv("LEARNING_CELERY_WORKER_HEALTHCHECK_TIMEOUT_SECONDS", "1.0")),
)
_local_tasks_inline = os.getenv("LEARNING_LOCAL_TASKS_INLINE")
LEARNING_LOCAL_TASKS_INLINE = (
    None
    if _local_tasks_inline is None
    else _parse_bool(_local_tasks_inline, False)
)
LEARNING_MATERIAL_MAX_UPLOAD_BYTES = _parse_int(os.getenv("LEARNING_MATERIAL_MAX_UPLOAD_BYTES"), 20 * 1024 * 1024)
LEARNING_MATERIAL_STORAGE_SHARED = _parse_bool(os.getenv("LEARNING_MATERIAL_STORAGE_SHARED"), False)
LEARNING_ADAPTIVE_TRACE = _parse_bool(os.getenv("LEARNING_ADAPTIVE_TRACE"), True)
LEARNING_SELF_ASSESSMENT_REPORT_TRACE = _parse_bool(
    os.getenv("LEARNING_SELF_ASSESSMENT_REPORT_TRACE"),
    LEARNING_ADAPTIVE_TRACE,
)

# Adaptive Tutor Agent V2 is the only student Chat runtime. The release
# manifest persists the exact values used by every run.
LEARNING_AGENT_V2_ENABLED = _parse_bool(os.getenv("LEARNING_AGENT_V2_ENABLED"), True)
LEARNING_AGENT_MODEL = os.getenv("LEARNING_AGENT_MODEL", "gpt-5.6-sol")
LEARNING_AGENT_REASONING_EFFORT = os.getenv("LEARNING_AGENT_REASONING_EFFORT", "medium")
# Agents SDK counts the final model response as a turn. Five SDK turns enforce
# the product limit of at most four tool rounds followed by one final answer.
LEARNING_AGENT_MAX_TURNS = _parse_int(os.getenv("LEARNING_AGENT_MAX_TURNS"), 5)
LEARNING_AGENT_MAX_TOOL_CALLS = _parse_int(os.getenv("LEARNING_AGENT_MAX_TOOL_CALLS"), 6)
LEARNING_AGENT_MAX_SKILLS = _parse_int(os.getenv("LEARNING_AGENT_MAX_SKILLS"), 2)
LEARNING_AGENT_TIMEOUT_SECONDS = _parse_int(os.getenv("LEARNING_AGENT_TIMEOUT_SECONDS"), 120)
LEARNING_MICRO_CHECK_GRADER_MODEL = os.getenv("LEARNING_MICRO_CHECK_GRADER_MODEL", "gpt-4.1-2025-04-14")
LEARNING_MICRO_CHECK_GRADER_TIMEOUT_SECONDS = _parse_int(
    os.getenv("LEARNING_MICRO_CHECK_GRADER_TIMEOUT_SECONDS"),
    8,
)
LEARNING_AGENT_INTERRUPTION_TTL_SECONDS = _parse_int(
    os.getenv("LEARNING_AGENT_INTERRUPTION_TTL_SECONDS"),
    24 * 60 * 60,
)
LEARNING_AGENT_CHECKPOINT_ENCRYPTION_KEY = os.getenv("LEARNING_AGENT_CHECKPOINT_ENCRYPTION_KEY", "").strip()
LEARNING_AGENT_RELEASE_NAME = os.getenv("LEARNING_AGENT_RELEASE_NAME", "adaptive-tutor-v2.1.0")
# Application-managed conversation replay keeps model context scoped,
# encrypted at rest and independent from provider-side retention.
LEARNING_AGENT_MEMORY_RECENT_TURNS = _parse_int(os.getenv("LEARNING_AGENT_MEMORY_RECENT_TURNS"), 8)
LEARNING_AGENT_MEMORY_COMPACTED_TURNS = _parse_int(os.getenv("LEARNING_AGENT_MEMORY_COMPACTED_TURNS"), 16)
LEARNING_AGENT_MEMORY_ROLLING_TURNS = _parse_int(os.getenv("LEARNING_AGENT_MEMORY_ROLLING_TURNS"), 64)
LEARNING_AGENT_MEMORY_OLDER_TOPICS = _parse_int(os.getenv("LEARNING_AGENT_MEMORY_OLDER_TOPICS"), 64)
LEARNING_AGENT_MEMORY_OLDER_TOPICS_IN_CONTEXT = _parse_int(
    os.getenv("LEARNING_AGENT_MEMORY_OLDER_TOPICS_IN_CONTEXT"),
    12,
)
LEARNING_AGENT_MEMORY_MAX_CONTEXT_CHARS = _parse_int(
    os.getenv("LEARNING_AGENT_MEMORY_MAX_CONTEXT_CHARS"),
    24_000,
)

LEARNING_LLM_MAX_CONCURRENCY = _parse_int(os.getenv("LEARNING_LLM_MAX_CONCURRENCY"), 32)
LEARNING_LLM_DEFAULT_TIMEOUT_SECONDS = _parse_int(os.getenv("LEARNING_LLM_DEFAULT_TIMEOUT_SECONDS"), 45)
LEARNING_LLM_MAX_RETRIES = _parse_int(os.getenv("LEARNING_LLM_MAX_RETRIES"), 3)
LEARNING_LLM_BACKOFF_BASE_SECONDS = float(os.getenv("LEARNING_LLM_BACKOFF_BASE_SECONDS", "0.75"))
LEARNING_CONCEPT_REMOTE_EMBEDDINGS_ENABLED = _parse_bool(
    os.getenv("LEARNING_CONCEPT_REMOTE_EMBEDDINGS_ENABLED"),
    True,
)
LEARNING_CONCEPT_EMBEDDING_MODEL = os.getenv("LEARNING_CONCEPT_EMBEDDING_MODEL", "text-embedding-3-small")
LEARNING_CONCEPT_EMBEDDING_DIMENSIONS = _parse_int(os.getenv("LEARNING_CONCEPT_EMBEDDING_DIMENSIONS"), 256)
LEARNING_CONCEPT_TAXONOMY_MAX_NODES = _parse_int(os.getenv("LEARNING_CONCEPT_TAXONOMY_MAX_NODES"), 2048)
LEARNING_CONCEPT_IDENTITY_MODEL = os.getenv("LEARNING_CONCEPT_IDENTITY_MODEL", "gpt-4.1-2025-04-14")
LEARNING_CONCEPT_MAP_MODEL = os.getenv("LEARNING_CONCEPT_MAP_MODEL", OPENAI_MODEL).strip()
LEARNING_CONCEPT_IDENTITY_TIMEOUT_SECONDS = _parse_int(os.getenv("LEARNING_CONCEPT_IDENTITY_TIMEOUT_SECONDS"), 8)
LEARNING_CONCEPT_IDENTITY_MAX_ATTEMPTS = _parse_int(os.getenv("LEARNING_CONCEPT_IDENTITY_MAX_ATTEMPTS"), 1)
LEARNING_LLM_ROUTE_LIMITS = {
    "adaptive.grader": _parse_int(os.getenv("LEARNING_LLM_ADAPTIVE_GRADER_MAX_CONCURRENCY"), 8),
    "adaptive.probe": _parse_int(os.getenv("LEARNING_LLM_ADAPTIVE_PROBE_MAX_CONCURRENCY"), 4),
    "chat.answer": _parse_int(os.getenv("LEARNING_LLM_CHAT_MAX_CONCURRENCY"), 24),
    "chat.analysis": _parse_int(os.getenv("LEARNING_LLM_CHAT_ANALYSIS_MAX_CONCURRENCY"), 12),
    "self_assessment.report": _parse_int(os.getenv("LEARNING_LLM_ASSESSMENT_MAX_CONCURRENCY"), 12),
    "learning_goal.assets": _parse_int(os.getenv("LEARNING_LLM_GOAL_ASSET_MAX_CONCURRENCY"), 8),
    "concept_map.generate": _parse_int(os.getenv("LEARNING_LLM_CONCEPT_MAP_MAX_CONCURRENCY"), 4),
    "adaptive.concept_embedding": _parse_int(os.getenv("LEARNING_LLM_CONCEPT_EMBEDDING_MAX_CONCURRENCY"), 8),
    "adaptive.concept_identity": _parse_int(os.getenv("LEARNING_LLM_CONCEPT_IDENTITY_MAX_CONCURRENCY"), 8),
    "agent.tutor_v2": _parse_int(os.getenv("LEARNING_LLM_AGENT_TUTOR_MAX_CONCURRENCY"), 12),
}
LEARNING_CHAT_JOB_POLL_TIMEOUT_SECONDS = _parse_int(os.getenv("LEARNING_CHAT_JOB_POLL_TIMEOUT_SECONDS"), 60)
LEARNING_CHAT_FALLBACK_AFTER_SECONDS = _parse_int(os.getenv("LEARNING_CHAT_FALLBACK_AFTER_SECONDS"), 45)
LEARNING_REMOTE_CONVERSATION_MIRROR_ENABLED = _parse_bool(
    os.getenv("LEARNING_REMOTE_CONVERSATION_MIRROR_ENABLED"),
    False,
)
LEARNING_CONVERSATION_RETENTION_DAYS = _parse_int(
    os.getenv("LEARNING_CONVERSATION_RETENTION_DAYS"),
    30,
)
# P5 exposes no HTTP endpoint or real provider connector yet.  The only
# executable external action is the deterministic local sandbox and it remains
# independently kill-switched even when a proposal has explicit approval.
LEARNING_MCP_HTTP_TRANSPORT_ENABLED = _parse_bool(
    os.getenv("LEARNING_MCP_HTTP_TRANSPORT_ENABLED"),
    False,
)
LEARNING_EXTERNAL_NETWORK_CONNECTORS_ENABLED = _parse_bool(
    os.getenv("LEARNING_EXTERNAL_NETWORK_CONNECTORS_ENABLED"),
    False,
)
LEARNING_EXTERNAL_SANDBOX_WRITES_ENABLED = _parse_bool(
    os.getenv("LEARNING_EXTERNAL_SANDBOX_WRITES_ENABLED"),
    False,
)

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
_staticfiles_backend = (
    os.getenv("DJANGO_STATICFILES_STORAGE")
    or (
        "django.contrib.staticfiles.storage.StaticFilesStorage"
        if RUNNING_TESTS
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )
)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": _staticfiles_backend,
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_SECURE = _parse_bool(os.getenv("DJANGO_SESSION_COOKIE_SECURE"), _SECURE_DEPLOYMENT_DEFAULT)
CSRF_COOKIE_SECURE = _parse_bool(os.getenv("DJANGO_CSRF_COOKIE_SECURE"), _SECURE_DEPLOYMENT_DEFAULT)
SECURE_SSL_REDIRECT = _parse_bool(os.getenv("DJANGO_SECURE_SSL_REDIRECT"), _SECURE_DEPLOYMENT_DEFAULT)
SECURE_HSTS_SECONDS = _parse_int(
    os.getenv("DJANGO_SECURE_HSTS_SECONDS"),
    31536000 if _SECURE_DEPLOYMENT_DEFAULT else 0,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _parse_bool(
    os.getenv("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS"),
    _SECURE_DEPLOYMENT_DEFAULT,
)
SECURE_HSTS_PRELOAD = _parse_bool(os.getenv("DJANGO_SECURE_HSTS_PRELOAD"), _SECURE_DEPLOYMENT_DEFAULT)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = _parse_bool(os.getenv("DJANGO_USE_X_FORWARDED_HOST"), False)
X_FRAME_OPTIONS = "DENY"

MESSAGE_TAGS = {
    message_constants.ERROR: "danger",
}
