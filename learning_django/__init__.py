"""Django project package for the Learning Workflow Demo."""
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    # PyMySQL isn't required until database access is configured.
    pass

try:
    from .celery import app as celery_app
except ImportError:
    celery_app = None

__all__ = ("celery_app",)
