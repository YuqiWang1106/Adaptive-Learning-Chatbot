from django.apps import AppConfig


class PersistenceConfig(AppConfig):
    """Own the database schema while preserving its historical app label."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "learning_apps.persistence"
    label = "main"
    verbose_name = "Application Persistence"
