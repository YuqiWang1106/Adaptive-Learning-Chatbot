from django.urls import path

from . import api
from . import mcp_api


app_name = "adaptive_agent"

urlpatterns = [
    path("mcp/adaptive-learning", mcp_api.adaptive_learning_mcp, name="adaptive_learning_mcp"),
    path("learning-goals/<int:goal_id>/agent-runs", api.create_run, name="create_run"),
    path("agent-runs/<str:run_id>", api.get_run, name="get_run"),
    path("agent-runs/<str:run_id>/events", api.run_events, name="run_events"),
    path(
        "agent-runs/<str:run_id>/clarifications/<str:interruption_id>",
        api.answer_clarification,
        name="answer_clarification",
    ),
    path(
        "agent-runs/<str:run_id>/approvals/<str:interruption_id>",
        api.resolve_approval,
        name="resolve_approval",
    ),
    path("agent-runs/<str:run_id>/cancel", api.cancel_run, name="cancel_run"),
    path(
        "learning-goals/<int:goal_id>/micro-checks/active",
        api.get_active_micro_check,
        name="active_micro_check",
    ),
    path("micro-checks/<str:check_id>/answer", api.answer_learning_check, name="answer_micro_check"),
    path("micro-checks/<str:check_id>/skip", api.skip_learning_check, name="skip_micro_check"),
    path(
        "learning-goals/<int:goal_id>/probe-offers/active",
        api.get_active_probe_offer,
        name="active_probe_offer",
    ),
    path("probe-offers/<str:offer_id>/accept", api.accept_review_offer, name="accept_probe_offer"),
    path("probe-offers/<str:offer_id>/snooze", api.snooze_review_offer, name="snooze_probe_offer"),
    path("probe-offers/<str:offer_id>/dismiss", api.dismiss_review_offer, name="dismiss_probe_offer"),
]
