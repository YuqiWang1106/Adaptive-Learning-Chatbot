from __future__ import annotations

from datetime import timedelta
import time
from typing import Any

from django.db import IntegrityError, OperationalError, close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from learning_apps.infrastructure.services.task_dispatcher import dispatch_task
from learning_apps.infrastructure.services.stable_hash import stable_sha256
from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    AdaptiveProbeOffer,
    ConceptRegistryEntry,
    LearnerMasteryState,
    LearnerPerceivedState,
    LearningGoal,
    UserProfile,
)

from .probe_service import (
    FORGETTING_PRIORITY_THRESHOLD,
    PRIORITY_THRESHOLD,
    _build_probe_candidates,
)
from .review_scheduler import PROBE_EXPIRY_INTERVAL, review_due_for_goal


PROBE_OFFER_POLICY_VERSION = "probe-offer-v2.0.0"
PROBE_OFFER_SNOOZE = timedelta(hours=24)
PROBE_OFFER_DISMISS_SUPPRESSION = timedelta(days=3)


class ProbeOfferError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _open_scope_key(user_id: int, goal_id: int) -> str:
    return f"{int(user_id)}:{int(goal_id)}"


def _state_fingerprint(user: UserProfile, goal: LearningGoal) -> str:
    rows = list(
        LearnerMasteryState.objects.filter(
            user=user,
            learning_goal=goal,
            eligible_evidence_count__gt=0,
        ).order_by("concept_key").values(
            "concept_key",
            "quality_score",
            "mastery_confidence",
            "eligible_evidence_count",
            "weakest_dimension",
            "updated_at",
        )
    )
    return stable_sha256(rows)


def _initial_fingerprint(perceived: LearnerPerceivedState, concept_key: str) -> str:
    return stable_sha256(
        {
            "policy": PROBE_OFFER_POLICY_VERSION,
            "perceived_state_id": perceived.id,
            "assessment_id": perceived.source_assessment_id,
            "decision_id": perceived.evidence_decision_id,
            "taxonomy": perceived.taxonomy_sha256,
            "concept_key": concept_key,
        }
    )


def _lowest_perceived_dimension(perceived: LearnerPerceivedState | None) -> str:
    if not perceived or not isinstance(perceived.dimension_scores, dict):
        return ""
    dimensions = ("facts", "procedures", "strategies", "rationales")
    eligible = []
    summary = perceived.diagnostic_summary if isinstance(perceived.diagnostic_summary, dict) else {}
    for dimension in dimensions:
        item = summary.get(dimension) if isinstance(summary, dict) else {}
        evidence_state = str(item.get("evidence_sufficiency") or "") if isinstance(item, dict) else ""
        if evidence_state in {"thin", "insufficient"}:
            continue
        try:
            eligible.append((dimension, float(perceived.dimension_scores[dimension])))
        except (KeyError, TypeError, ValueError):
            continue
    return min(
        eligible,
        key=lambda item: (item[1], dimensions.index(item[0])),
    )[0] if eligible else ""


def _has_admitted_probe(user: UserProfile, goal: LearningGoal, concept_key: str) -> bool:
    from .mastery_evidence_policy import evidence_is_admitted

    rows = AdaptiveInteractionEvent.objects.filter(
        user=user,
        learning_goal=goal,
        concept_key=concept_key,
        source=AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
    ).only("metadata")
    return any(evidence_is_admitted(row.metadata) for row in rows)


def _create_initial_calibration_offer_once(run) -> AdaptiveProbeOffer | None:
    """Create one non-blocking calibration offer after a verified-focus turn."""

    if run.status != "completed" or run.origin != "normal":
        return None
    manifest = run.adaptive_context_manifest if isinstance(run.adaptive_context_manifest, dict) else {}
    candidates = {str(manifest.get("focus_concept_key") or "")}
    for item in run.evidence_manifest or []:
        if not isinstance(item, dict):
            continue
        if item.get("category") == "concept_identity" and item.get("status") == "accepted":
            candidates.update(str(value) for value in item.get("concept_keys", []) if value)
        elif item.get("category") == "teaching_state":
            candidates.update(str(value) for value in item.get("concept_keys", []) if value)
    candidates.discard("")
    if len(candidates) != 1:
        return None
    concept_key = next(iter(candidates))
    user, goal = run.user, run.learning_goal
    registry = ConceptRegistryEntry.objects.filter(
        user=user,
        learning_goal=goal,
        concept_key=concept_key,
        status=ConceptRegistryEntry.STATUS_VERIFIED,
    ).first()
    if not registry or _has_admitted_probe(user, goal, concept_key):
        return None
    perceived = LearnerPerceivedState.objects.filter(
        user=user,
        learning_goal=goal,
        authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
        mastery_write_authorized=False,
    ).first()
    if not perceived:
        return None
    target_dimension = _lowest_perceived_dimension(perceived)
    if not target_dimension:
        return None
    now = timezone.now()
    with transaction.atomic():
        goal = LearningGoal.objects.select_for_update().get(pk=goal.pk, user=user)
        existing = AdaptiveProbeOffer.objects.filter(
            user=user,
            learning_goal=goal,
            status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
        ).order_by("-offered_at").first()
        if existing:
            return existing
        if _has_admitted_probe(user, goal, concept_key):
            return None
        fingerprint = _initial_fingerprint(perceived, concept_key)
        idempotency_key = stable_sha256(
            {
                "policy": PROBE_OFFER_POLICY_VERSION,
                "kind": "initial_calibration",
                "user": user.user_id,
                "goal": goal.id,
                "concept": concept_key,
                "perceived": fingerprint,
            }
        )[:96]
        try:
            with transaction.atomic():
                return AdaptiveProbeOffer.objects.create(
                    user=user,
                    learning_goal=goal,
                    policy_version=PROBE_OFFER_POLICY_VERSION,
                    due_trigger="initial_calibration",
                    due_reason="A short calibration can turn self-assessment guidance into observed learning evidence.",
                    target_concept_key=concept_key,
                    target_concept_label=str(registry.concept_label or "")[:180],
                    target_dimension=target_dimension,
                    candidate_score=1.0,
                    candidate_components_snapshot={
                        "policy": "perceived_lowest_dimension",
                        "source_assessment_id": perceived.source_assessment_id,
                    },
                    mastery_state_fingerprint=fingerprint,
                    status=AdaptiveProbeOffer.STATUS_PENDING,
                    source="initial_calibration",
                    idempotency_key=idempotency_key,
                    open_scope_key=_open_scope_key(user.user_id, goal.id),
                    expires_at=now + PROBE_EXPIRY_INTERVAL,
                    mastery_write_authorized=False,
                )
        except IntegrityError:
            return AdaptiveProbeOffer.objects.filter(
                user=user,
                learning_goal=goal,
                status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
            ).first()


def create_initial_calibration_offer(run) -> AdaptiveProbeOffer | None:
    """Retry SQLite lock contention while DB constraints preserve one Offer.

    Production databases serialize on the goal row. SQLite does not implement
    ``SELECT ... FOR UPDATE``, so local acceptance and multi-tab development
    can observe a short-lived ``database is locked`` error instead. Retry only
    that transient condition; every other database error still fails closed.
    """

    for attempt in range(8):
        try:
            return _create_initial_calibration_offer_once(run)
        except OperationalError as exc:
            message = str(exc).casefold()
            if not any(token in message for token in ("locked", "busy")) or attempt == 7:
                raise
            close_old_connections()
            time.sleep(0.01 * (attempt + 1))
    return None


def _preferred_initial_concept(
    user: UserProfile,
    goal: LearningGoal,
) -> ConceptRegistryEntry | None:
    entries = list(
        ConceptRegistryEntry.objects.filter(
            user=user,
            learning_goal=goal,
            status=ConceptRegistryEntry.STATUS_VERIFIED,
        ).order_by("id")
    )

    def priority(entry: ConceptRegistryEntry) -> tuple[int, int, int]:
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        node_type = str(metadata.get("concept_map_node_type") or "").casefold()
        type_rank = 0 if node_type == "prerequisite" else 1 if node_type == "core" else 2
        source_rank = 0 if entry.source == ConceptRegistryEntry.SOURCE_CONCEPT_MAP else 1
        return type_rank, source_rank, entry.id

    return min(entries, key=priority) if entries else None


def ensure_initial_calibration_offer_for_assessment(
    assessment_id: int,
) -> AdaptiveProbeOffer | None:
    """Idempotently offer one optional calibration after assessment commit."""

    perceived = (
        LearnerPerceivedState.objects.select_related(
            "user",
            "learning_goal",
            "source_assessment",
        )
        .filter(
            source_assessment_id=int(assessment_id),
            authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
            mastery_write_authorized=False,
        )
        .first()
    )
    if not perceived:
        return None
    target_dimension = _lowest_perceived_dimension(perceived)
    if not target_dimension:
        return None
    user, goal = perceived.user, perceived.learning_goal
    registry = _preferred_initial_concept(user, goal)
    if not registry:
        return None
    fingerprint = _initial_fingerprint(perceived, registry.concept_key)
    idempotency_key = stable_sha256(
        {
            "policy": PROBE_OFFER_POLICY_VERSION,
            "kind": "initial_calibration",
            "assessment": perceived.source_assessment_id,
            "concept": registry.concept_key,
            "dimension": target_dimension,
            "perceived": fingerprint,
        }
    )[:96]
    now = timezone.now()
    with transaction.atomic():
        LearningGoal.objects.select_for_update().get(pk=goal.pk, user=user)
        existing = AdaptiveProbeOffer.objects.filter(
            user=user,
            learning_goal=goal,
            status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
        ).order_by("-offered_at").first()
        if existing:
            return existing
        if _has_admitted_probe(user, goal, registry.concept_key):
            return None
        try:
            return AdaptiveProbeOffer.objects.create(
                user=user,
                learning_goal=goal,
                policy_version=PROBE_OFFER_POLICY_VERSION,
                due_trigger="initial_calibration",
                due_reason="A 1-minute check can compare your self-report with observed evidence.",
                target_concept_key=registry.concept_key,
                target_concept_label=str(registry.concept_label or registry.concept_key)[:180],
                target_dimension=target_dimension,
                candidate_score=1.0,
                candidate_components_snapshot={
                    "policy": "assessment_v2_initial_calibration",
                    "source_assessment_id": perceived.source_assessment_id,
                    "evidence_sufficiency": (
                        perceived.diagnostic_summary.get(target_dimension, {}).get(
                            "evidence_sufficiency",
                            "",
                        )
                        if isinstance(perceived.diagnostic_summary, dict)
                        else ""
                    ),
                },
                mastery_state_fingerprint=fingerprint,
                status=AdaptiveProbeOffer.STATUS_PENDING,
                source="initial_calibration",
                idempotency_key=idempotency_key,
                open_scope_key=_open_scope_key(user.user_id, goal.id),
                expires_at=now + PROBE_EXPIRY_INTERVAL,
                mastery_write_authorized=False,
            )
        except IntegrityError:
            return AdaptiveProbeOffer.objects.filter(
                user=user,
                learning_goal=goal,
                status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
            ).first()


def _current_candidate(user: UserProfile, goal: LearningGoal, *, due_by_forgetting: bool) -> dict[str, Any] | None:
    verified = set(
        ConceptRegistryEntry.objects.filter(
            user=user,
            learning_goal=goal,
            status=ConceptRegistryEntry.STATUS_VERIFIED,
        ).values_list("concept_key", flat=True)
    )
    admitted = set(
        LearnerMasteryState.objects.filter(
            user=user,
            learning_goal=goal,
            concept_key__in=verified,
            eligible_evidence_count__gt=0,
        ).values_list("concept_key", flat=True)
    )
    if not admitted:
        return None
    from learning_apps.persistence.models import AdaptiveProbe

    recent = list(AdaptiveProbe.objects.filter(user=user, learning_goal=goal).order_by("-created_at", "-id")[:3])
    candidates = _build_probe_candidates(
        user=user,
        goal=goal,
        recent_probes=recent,
        allowed_concept_keys=admitted,
    )
    if not candidates:
        return None
    candidate = candidates[0]
    threshold = FORGETTING_PRIORITY_THRESHOLD if due_by_forgetting else PRIORITY_THRESHOLD
    return candidate if float(candidate.get("priority_score") or 0.0) >= threshold else None


def serialize_probe_offer(offer: AdaptiveProbeOffer) -> dict[str, Any]:
    return {
        "offer_id": offer.offer_id,
        "status": offer.status,
        "reason": offer.due_reason,
        "trigger": offer.due_trigger,
        "target": {
            "concept_label": offer.target_concept_label or offer.target_concept_key.replace("_", " ").title(),
            "dimension": offer.target_dimension,
        },
        "estimated_minutes": "1" if offer.due_trigger == "initial_calibration" else "2–3",
        "offered_at": offer.offered_at.isoformat() if offer.offered_at else "",
        "snoozed_until": offer.snoozed_until.isoformat() if offer.snoozed_until else "",
        "expires_at": offer.expires_at.isoformat(),
        "probe_id": offer.resulting_probe_id,
        "mastery_write_authorized": False,
    }


def create_probe_offer(
    username: str,
    learning_goal_id: int,
    *,
    now=None,
    source: str = "review_scheduler",
) -> AdaptiveProbeOffer | None:
    fixed_now = now or timezone.now()
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(id=int(learning_goal_id), user=user).first() if user else None
    if not user or not goal:
        return None
    with transaction.atomic():
        goal = LearningGoal.objects.select_for_update().get(id=goal.id, user=user)
        existing = AdaptiveProbeOffer.objects.filter(
            user=user,
            learning_goal=goal,
            status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
        ).order_by("-offered_at").first()
        if existing:
            return existing
        snoozed = AdaptiveProbeOffer.objects.filter(
            user=user,
            learning_goal=goal,
            status=AdaptiveProbeOffer.STATUS_SNOOZED,
            snoozed_until__gt=fixed_now,
        ).order_by("-snoozed_until").first()
        if snoozed:
            return None
        decision = review_due_for_goal(user=user, goal=goal, now=fixed_now)
        if not decision.due:
            return None
        candidate = _current_candidate(user, goal, due_by_forgetting=decision.due_by_forgetting)
        if not candidate:
            return None
        dismissed_after = fixed_now - PROBE_OFFER_DISMISS_SUPPRESSION
        if AdaptiveProbeOffer.objects.filter(
            user=user,
            learning_goal=goal,
            target_concept_key=candidate["concept_key"],
            status=AdaptiveProbeOffer.STATUS_DISMISSED,
            dismissed_at__gt=dismissed_after,
        ).exists():
            return None
        fingerprint = _state_fingerprint(user, goal)
        idempotency_key = stable_sha256(
            {
                "policy": PROBE_OFFER_POLICY_VERSION,
                "user": user.user_id,
                "goal": goal.id,
                "trigger": decision.trigger,
                "concept": candidate["concept_key"],
                "dimension": candidate["target_dimension"],
                "state": fingerprint,
                # A due cycle can legitimately recur after snooze/dismiss/expiry.
                # The active-scope lock still prevents concurrent duplicates.
                "due_cycle_date": fixed_now.date().isoformat(),
            }
        )[:96]
        try:
            # Isolate the unique-key race in a savepoint. Catching an
            # IntegrityError directly in the outer atomic block would leave
            # that transaction unusable before the winner can be read.
            with transaction.atomic():
                offer = AdaptiveProbeOffer.objects.create(
                    user=user,
                    learning_goal=goal,
                    policy_version=PROBE_OFFER_POLICY_VERSION,
                    due_trigger=decision.trigger,
                    due_reason=decision.reason,
                    target_concept_key=candidate["concept_key"],
                    target_concept_label=str(candidate.get("concept_label") or "")[:180],
                    target_dimension=candidate["target_dimension"],
                    candidate_score=float(candidate["priority_score"]),
                    candidate_components_snapshot=candidate.get("priority_components") or {},
                    mastery_state_fingerprint=fingerprint,
                    status=AdaptiveProbeOffer.STATUS_PENDING,
                    source=str(source or "review_scheduler")[:64],
                    idempotency_key=idempotency_key,
                    open_scope_key=_open_scope_key(user.user_id, goal.id),
                    expires_at=fixed_now + PROBE_EXPIRY_INTERVAL,
                    mastery_write_authorized=False,
                )
        except IntegrityError:
            return AdaptiveProbeOffer.objects.filter(
                user=user,
                learning_goal=goal,
                status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
            ).first()
    return offer


def active_probe_offer(username: str, learning_goal_id: int, *, for_chat: bool = False) -> AdaptiveProbeOffer | None:
    now = timezone.now()
    with transaction.atomic():
        queryset = AdaptiveProbeOffer.objects.filter(
            user__username=username,
            learning_goal_id=int(learning_goal_id),
            status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
        ).order_by("-offered_at")
        if for_chat:
            queryset = queryset.select_for_update()
        offer = queryset.first()
        if not offer:
            return None
        if offer.expires_at <= now:
            AdaptiveProbeOffer.objects.filter(offer_id=offer.offer_id).update(
                status=AdaptiveProbeOffer.STATUS_EXPIRED,
                open_scope_key=None,
                expired_at=now,
            )
            return None
        if for_chat and offer.status == AdaptiveProbeOffer.STATUS_PENDING:
            if offer.last_presented_at and offer.last_presented_at > now - timedelta(hours=24):
                return None
            AdaptiveProbeOffer.objects.filter(offer_id=offer.offer_id).update(
                last_presented_at=now,
                presented_count=F("presented_count") + 1,
            )
            offer.refresh_from_db(fields=["last_presented_at", "presented_count"])
    return offer


def _dispatch_offer_generation(offer_id: str) -> None:
    from .probe_service import generate_probe_from_offer
    from .tasks import generate_probe_from_offer_task

    dispatch_task(
        generate_probe_from_offer_task,
        offer_id,
        fallback=lambda: generate_probe_from_offer(offer_id),
    )


def accept_probe_offer(*, username: str, offer_id: str) -> AdaptiveProbeOffer:
    dispatch = False
    terminal_error = ""
    with transaction.atomic():
        offer = AdaptiveProbeOffer.objects.select_for_update().select_related("user", "learning_goal").filter(
            offer_id=offer_id,
            user__username=username,
        ).first()
        if not offer:
            raise ProbeOfferError("probe_offer_not_found")
        if offer.status == AdaptiveProbeOffer.STATUS_ACCEPTED:
            # A prior worker/provider attempt may have failed after acceptance.
            # Re-dispatching is safe because generation locks the Offer and
            # returns the existing resulting Probe when one already exists.
            dispatch = True
        elif offer.status in {AdaptiveProbeOffer.STATUS_GENERATING, AdaptiveProbeOffer.STATUS_READY}:
            return offer
        elif offer.status != AdaptiveProbeOffer.STATUS_PENDING:
            raise ProbeOfferError("probe_offer_not_active")
        if offer.status == AdaptiveProbeOffer.STATUS_PENDING:
            now = timezone.now()
            if offer.expires_at <= now:
                offer.status = AdaptiveProbeOffer.STATUS_EXPIRED
                offer.open_scope_key = None
                offer.expired_at = now
                offer.save(update_fields=["status", "open_scope_key", "expired_at", "updated_at"])
                terminal_error = "probe_offer_expired"
            else:
                if offer.due_trigger == "initial_calibration":
                    perceived = LearnerPerceivedState.objects.filter(
                        user=offer.user,
                        learning_goal=offer.learning_goal,
                        authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
                    ).first()
                    valid_initial = bool(
                        perceived
                        and ConceptRegistryEntry.objects.filter(
                            user=offer.user,
                            learning_goal=offer.learning_goal,
                            concept_key=offer.target_concept_key,
                            status=ConceptRegistryEntry.STATUS_VERIFIED,
                        ).exists()
                        and not _has_admitted_probe(offer.user, offer.learning_goal, offer.target_concept_key)
                    )
                    initial_target_dimension = _lowest_perceived_dimension(perceived)
                    valid_initial = bool(valid_initial and initial_target_dimension)
                    candidate = None
                    decision = None
                else:
                    decision = review_due_for_goal(user=offer.user, goal=offer.learning_goal, now=now)
                    candidate = _current_candidate(offer.user, offer.learning_goal, due_by_forgetting=decision.due_by_forgetting)
                    valid_initial = False
                if not valid_initial and (decision is None or not decision.due or not candidate):
                    offer.status = AdaptiveProbeOffer.STATUS_SUPERSEDED
                    offer.open_scope_key = None
                    offer.save(update_fields=["status", "open_scope_key", "updated_at"])
                    terminal_error = "no_longer_due"
                elif valid_initial:
                    offer.mastery_state_fingerprint = _initial_fingerprint(perceived, offer.target_concept_key)
                    offer.target_dimension = initial_target_dimension
                    offer.status = AdaptiveProbeOffer.STATUS_ACCEPTED
                    offer.accepted_at = now
                    offer.save(update_fields=[
                        "mastery_state_fingerprint",
                        "target_dimension",
                        "status",
                        "accepted_at",
                        "updated_at",
                    ])
                    dispatch = True
                else:
                    offer.target_concept_key = candidate["concept_key"]
                    offer.target_concept_label = str(candidate.get("concept_label") or "")[:180]
                    offer.target_dimension = candidate["target_dimension"]
                    offer.candidate_score = float(candidate["priority_score"])
                    offer.candidate_components_snapshot = candidate.get("priority_components") or {}
                    offer.mastery_state_fingerprint = _state_fingerprint(offer.user, offer.learning_goal)
                    offer.status = AdaptiveProbeOffer.STATUS_ACCEPTED
                    offer.accepted_at = now
                    offer.save(update_fields=[
                        "target_concept_key", "target_concept_label", "target_dimension", "candidate_score",
                        "candidate_components_snapshot", "mastery_state_fingerprint", "status", "accepted_at", "updated_at",
                    ])
                    dispatch = True
    if terminal_error:
        raise ProbeOfferError(terminal_error)
    if dispatch:
        _dispatch_offer_generation(offer.offer_id)
    offer.refresh_from_db()
    return offer


def snooze_probe_offer(*, username: str, offer_id: str) -> AdaptiveProbeOffer:
    with transaction.atomic():
        offer = AdaptiveProbeOffer.objects.select_for_update().filter(offer_id=offer_id, user__username=username).first()
        if not offer:
            raise ProbeOfferError("probe_offer_not_found")
        if offer.status != AdaptiveProbeOffer.STATUS_PENDING:
            return offer
        now = timezone.now()
        offer.status = AdaptiveProbeOffer.STATUS_SNOOZED
        offer.open_scope_key = None
        offer.snoozed_at = now
        offer.snoozed_until = now + PROBE_OFFER_SNOOZE
        offer.save(update_fields=["status", "open_scope_key", "snoozed_at", "snoozed_until", "updated_at"])
    return offer


def dismiss_probe_offer(*, username: str, offer_id: str) -> AdaptiveProbeOffer:
    with transaction.atomic():
        offer = AdaptiveProbeOffer.objects.select_for_update().filter(offer_id=offer_id, user__username=username).first()
        if not offer:
            raise ProbeOfferError("probe_offer_not_found")
        if offer.status != AdaptiveProbeOffer.STATUS_PENDING:
            return offer
        offer.status = AdaptiveProbeOffer.STATUS_DISMISSED
        offer.open_scope_key = None
        offer.dismissed_at = timezone.now()
        offer.save(update_fields=["status", "open_scope_key", "dismissed_at", "updated_at"])
    return offer


__all__ = [
    "PROBE_OFFER_POLICY_VERSION",
    "ProbeOfferError",
    "accept_probe_offer",
    "active_probe_offer",
    "create_initial_calibration_offer",
    "create_probe_offer",
    "dismiss_probe_offer",
    "ensure_initial_calibration_offer_for_assessment",
    "serialize_probe_offer",
    "snooze_probe_offer",
]
