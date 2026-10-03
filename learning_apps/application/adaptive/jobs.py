"""Application entrypoints used by adaptive-learning workers."""

from learning_apps.adaptive_learning.event_service import run_adaptive_grading
from learning_apps.adaptive_learning.probe_offer_service import create_probe_offer
from learning_apps.adaptive_learning.probe_service import generate_probe_from_offer


def run_grading(payload: dict) -> None:
    run_adaptive_grading(payload)


def run_probe_offer(
    username: str,
    learning_goal_id: int,
    *,
    now=None,
    source: str = "celery",
):
    return create_probe_offer(
        username,
        learning_goal_id,
        now=now,
        source=source,
    )


def run_probe_generation(offer_id: str):
    return generate_probe_from_offer(offer_id)


__all__ = ["run_grading", "run_probe_generation", "run_probe_offer"]
