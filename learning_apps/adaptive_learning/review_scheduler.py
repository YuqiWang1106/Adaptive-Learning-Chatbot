"""Deterministic review-due policy and scheduler selection.

The policy is intentionally independent of Celery and chat.  A caller can
replay it with a fixed clock, while the task layer only coordinates database
work and probe generation.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps
from django.utils import timezone

from learning_apps.persistence.models import (
    AdaptiveProbe,
    AdaptiveProbeOffer,
    ConceptRegistryEntry,
    LearnerMasteryState,
    LearningGoal,
    UserProfile,
)


REVIEW_SCHEDULER_VERSION = "review_scheduler_v2"
CHAT_COUNT_THRESHOLD = 4
TIME_REVIEW_INTERVAL = timedelta(hours=24)
FORGETTING_REVIEW_INTERVAL = timedelta(days=3)
PROBE_EXPIRY_INTERVAL = timedelta(days=7)


@dataclass(frozen=True)
class ReviewDueDecision:
    due: bool
    trigger: str
    reason: str
    due_by_chat_count: bool
    due_by_time: bool
    due_by_forgetting: bool
    chat_count_since_last_probe: int
    last_probe_age_seconds: float | None
    state_age_seconds: float | None
    scheduler_version: str = REVIEW_SCHEDULER_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewGoalScanPage:
    """One keyset-paginated scheduler page.

    The old implementation sliced the first ``limit`` goals before checking
    due state, so every goal after that prefix could be starved forever.  A
    cursor makes the scan advance across the whole population without using
    an increasingly expensive SQL offset.
    """

    due_goals: list[tuple[UserProfile, LearningGoal, ReviewDueDecision]]
    scanned_goal_count: int
    first_goal_id: int
    last_goal_id: int
    next_cursor_id: int
    cycle_complete: bool


def _age_seconds(now: datetime, occurred_at: datetime | None) -> float | None:
    if occurred_at is None:
        return None
    return max(0.0, (now - occurred_at).total_seconds())


def compute_review_due(
    *,
    now: datetime,
    has_state: bool,
    has_prior_probe: bool,
    chat_count_since_last_probe: int,
    last_probe_at: datetime | None = None,
    state_updated_at: datetime | None = None,
) -> ReviewDueDecision:
    """Pure due/not-due policy with inclusive boundaries."""

    last_probe_age = _age_seconds(now, last_probe_at)
    state_age = _age_seconds(now, state_updated_at)
    chat_count = max(0, int(chat_count_since_last_probe or 0))
    due_by_chat_count = bool(has_state and chat_count >= CHAT_COUNT_THRESHOLD)
    due_by_time = bool(
        has_state
        and has_prior_probe
        and chat_count >= 1
        and last_probe_age is not None
        and last_probe_age >= TIME_REVIEW_INTERVAL.total_seconds()
    )
    due_by_forgetting = bool(has_state and state_age is not None and state_age >= FORGETTING_REVIEW_INTERVAL.total_seconds())
    if due_by_forgetting:
        trigger = "forgetting"
        reason = "state_age_at_or_above_forgetting_boundary"
    elif due_by_chat_count:
        trigger = "chat_count"
        reason = "substantive_agent_run_count_at_or_above_boundary"
    elif due_by_time:
        trigger = "time"
        reason = "last_probe_age_at_or_above_time_boundary"
    else:
        trigger = ""
        reason = "not_due"
    return ReviewDueDecision(
        due=bool(due_by_chat_count or due_by_time or due_by_forgetting),
        trigger=trigger,
        reason=reason,
        due_by_chat_count=due_by_chat_count,
        due_by_time=due_by_time,
        due_by_forgetting=due_by_forgetting,
        chat_count_since_last_probe=chat_count,
        last_probe_age_seconds=last_probe_age,
        state_age_seconds=state_age,
    )


def _substantive_run_count(user: UserProfile, goal: LearningGoal, *, after=None) -> int:
    LearningAgentRun = apps.get_model("adaptive_agent", "LearningAgentRun")
    queryset = LearningAgentRun.objects.filter(
        user=user,
        learning_goal=goal,
        status="completed",
        origin="normal",
    )
    if after is not None:
        queryset = queryset.filter(completed_at__gt=after)
    return queryset.count()


def review_due_for_goal(
    *,
    user: UserProfile,
    goal: LearningGoal,
    now: datetime | None = None,
) -> ReviewDueDecision:
    """Read current evidence and apply the pure due policy."""

    fixed_now = now or timezone.now()
    last_probe = AdaptiveProbe.objects.filter(user=user, learning_goal=goal).order_by("-created_at", "-id").first()
    chat_count = _substantive_run_count(
        user,
        goal,
        after=last_probe.created_at if last_probe and last_probe.created_at else None,
    )
    verified_keys = ConceptRegistryEntry.objects.filter(
        user=user,
        learning_goal=goal,
        status=ConceptRegistryEntry.STATUS_VERIFIED,
    ).values("concept_key")
    state = LearnerMasteryState.objects.filter(
        user=user,
        learning_goal=goal,
        concept_key__in=verified_keys,
        eligible_evidence_count__gt=0,
    ).order_by("updated_at", "id").first()
    return compute_review_due(
        now=fixed_now,
        has_state=state is not None,
        has_prior_probe=last_probe is not None,
        chat_count_since_last_probe=chat_count,
        last_probe_at=last_probe.created_at if last_probe else None,
        state_updated_at=state.updated_at if state else None,
    )


def expire_stale_pending_probes(*, now: datetime | None = None, limit: int = 500) -> int:
    """Move overdue pending probes to ``expired`` using the scheduler clock."""

    fixed_now = now or timezone.now()
    candidates = list(
        AdaptiveProbe.objects.filter(status=AdaptiveProbe.STATUS_PENDING)
        .order_by("created_at", "id")[: max(0, int(limit))]
    )
    expired_ids: list[int] = []
    for probe in candidates:
        expires_at = probe.expires_at or (probe.created_at + PROBE_EXPIRY_INTERVAL if probe.created_at else None)
        if expires_at and expires_at <= fixed_now:
            expired_ids.append(probe.id)
    if not expired_ids:
        return 0
    return AdaptiveProbe.objects.filter(
        id__in=expired_ids,
        status=AdaptiveProbe.STATUS_PENDING,
    ).update(status=AdaptiveProbe.STATUS_EXPIRED, completed_at=fixed_now)


def expire_stale_probe_offers(*, now: datetime | None = None, limit: int = 500) -> int:
    fixed_now = now or timezone.now()
    offer_ids = list(
        AdaptiveProbeOffer.objects.filter(
            status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
            expires_at__lte=fixed_now,
        ).order_by("expires_at").values_list("offer_id", flat=True)[: max(0, int(limit))]
    )
    if not offer_ids:
        return 0
    return AdaptiveProbeOffer.objects.filter(
        offer_id__in=offer_ids,
        status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
    ).update(
        status=AdaptiveProbeOffer.STATUS_EXPIRED,
        open_scope_key=None,
        expired_at=fixed_now,
    )


def iter_due_goals(*, now: datetime | None = None, limit: int = 100) -> list[tuple[UserProfile, LearningGoal, ReviewDueDecision]]:
    """Return up to ``limit`` due goals after considering the full population.

    This compatibility API is appropriate for offline snapshots.  Production
    periodic work uses :func:`scan_review_goal_page` so one task has bounded
    database work while successive tasks still cover every goal.
    """

    fixed_now = now or timezone.now()
    goals = LearningGoal.objects.filter(
        status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
    ).select_related("user").order_by("id")
    due_limit = max(0, int(limit or 0))
    due: list[tuple[UserProfile, LearningGoal, ReviewDueDecision]] = []
    for goal in goals.iterator(chunk_size=max(100, due_limit or 100)):
        decision = review_due_for_goal(user=goal.user, goal=goal, now=fixed_now)
        if decision.due:
            due.append((goal.user, goal, decision))
            if due_limit and len(due) >= due_limit:
                break
    return due


def scan_review_goal_page(
    *,
    now: datetime | None = None,
    cursor_id: int = 0,
    limit: int = 100,
) -> ReviewGoalScanPage:
    """Evaluate one bounded keyset page and return the next fair-scan cursor."""

    fixed_now = now or timezone.now()
    page_limit = max(1, int(limit or 100))
    base = LearningGoal.objects.filter(
        status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
    ).select_related("user").order_by("id")
    goals = list(base.filter(id__gt=max(0, int(cursor_id or 0)))[: page_limit + 1])
    # A cursor can outlive deleted/test data.  Wrap immediately instead of
    # wasting every future Beat tick on an empty range.
    if not goals and cursor_id:
        goals = list(base[: page_limit + 1])
    has_more = len(goals) > page_limit
    goals = goals[:page_limit]
    due: list[tuple[UserProfile, LearningGoal, ReviewDueDecision]] = []
    for goal in goals:
        decision = review_due_for_goal(user=goal.user, goal=goal, now=fixed_now)
        if decision.due:
            due.append((goal.user, goal, decision))
    first_id = int(goals[0].id) if goals else 0
    last_id = int(goals[-1].id) if goals else 0
    return ReviewGoalScanPage(
        due_goals=due,
        scanned_goal_count=len(goals),
        first_goal_id=first_id,
        last_goal_id=last_id,
        next_cursor_id=last_id if has_more else 0,
        cycle_complete=not has_more,
    )


def scheduler_snapshot(*, now: datetime | None = None, limit: int = 100) -> dict[str, Any]:
    """Return a serializable scan result used by Celery and offline evals."""

    fixed_now = now or timezone.now()
    expired = expire_stale_pending_probes(now=fixed_now)
    expired_offers = expire_stale_probe_offers(now=fixed_now)
    due = iter_due_goals(now=fixed_now, limit=limit)
    return {
        "scheduler_version": REVIEW_SCHEDULER_VERSION,
        "now": fixed_now.isoformat(),
        "expired_pending_count": expired,
        "expired_offer_count": expired_offers,
        "due_goal_count": len(due),
        "due_goals": [
            {
                "username": user.username,
                "learning_goal_id": goal.id,
                "decision": decision.as_dict(),
            }
            for user, goal, decision in due
        ],
    }
