from django.apps import AppConfig


class AdaptiveAgentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "learning_apps.adaptive_agent"
    label = "adaptive_agent"
    verbose_name = "Adaptive Tutor Agent V2"

    def ready(self) -> None:
        from .event_handlers import connect_domain_event_handlers

        connect_domain_event_handlers()
