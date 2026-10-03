"""Local conversation generations, fencing, deletion and provider cleanup.

The local row is the correctness boundary. A provider conversation is an
optional mirror and is never consulted to decide whether a write is valid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import logging
import uuid
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from learning_apps.persistence.models import (
    AIJob,
    AnswerGroundingDecision,
    LearningConversation,
    LearningGoal,
    UserHistory,
    UserProfile,
)
from learning_apps.chat.domain_events import conversation_fenced

from . import conversation_provider_service


logger = logging.getLogger(__name__)
CONVERSATION_POLICY_VERSION = "p2.4-conversation-v1"
DEFAULT_RETENTION_DAYS = 30
CLEANUP_LEASE_SECONDS = 120


class ConversationScopeError(ValueError):
    """Raised when the session owner does not own the requested goal."""


class ConversationFencedError(RuntimeError):
    """Raised when a worker tries to write into a cleared generation."""


@dataclass(frozen=True)
class ConversationClearResult:
    cleared: bool
    conversation_key: str = ""
    generation: int = 0
    next_generation: int = 1
    deleted_history_count: int = 0
    deleted_job_count: int = 0

    def to_dict(self) -> dict:
        return {
            "cleared": self.cleared,
            "generation": self.generation,
            "next_generation": self.next_generation,
            "deleted_history_count": self.deleted_history_count,
            "deleted_job_count": self.deleted_job_count,
        }


def _retention_days() -> int:
    return max(1, int(getattr(settings, "LEARNING_CONVERSATION_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)))


def _active_scope_key(user_id: int, learning_goal_id: int) -> str:
    digest = hashlib.sha256(f"conversation\0{user_id}\0{learning_goal_id}".encode()).hexdigest()
    return f"scope_{digest}"


def _local_conversation_key() -> str:
    return f"lc_{uuid.uuid4().hex}"


def _delete_stream_cache(job_ids: list[str]) -> None:
    if job_ids:
        cache.delete_many([f"chat:answer-stream:{job_id}" for job_id in job_ids])


def _dispatch_cleanup_reconcile() -> None:
    if not bool(getattr(settings, "LEARNING_USE_CELERY", False)):
        return
    try:
        from learning_apps.chat.tasks import reconcile_conversation_cleanup_task

        reconcile_conversation_cleanup_task.delay()
    except Exception as exc:  # beat remains the durable fallback
        logger.warning("Could not dispatch conversation cleanup reconcile: %s", exc.__class__.__name__)


def _scrub_and_tombstone_locked(
    conversation: LearningConversation,
    goal: LearningGoal,
    *,
    fixed_now,
) -> tuple[int, int, list[str]]:
    """Scrub one locked active generation; caller owns the transaction."""

    history_ids = list(
        UserHistory.objects.filter(conversation=conversation).values_list("id", flat=True)
    )
    if history_ids:
        AnswerGroundingDecision.objects.filter(user_history_id__in=history_ids).delete()
    deleted_history_count, _ = UserHistory.objects.filter(conversation=conversation).delete()
    jobs = AIJob.objects.filter(conversation=conversation)
    job_ids = list(jobs.values_list("job_id", flat=True))
    deleted_job_count, _ = jobs.delete()

    conversation.lifecycle = LearningConversation.LIFECYCLE_TOMBSTONED
    conversation.active_scope_key = None
    conversation.tombstoned_at = fixed_now
    conversation.retention_expires_at = fixed_now + timedelta(days=_retention_days())
    conversation.cleanup_lease_token = ""
    conversation.cleanup_lease_expires_at = None
    conversation.cleanup_next_attempt_at = fixed_now if conversation.provider_conversation_id else None
    conversation.provider_cleanup_status = (
        LearningConversation.CLEANUP_PENDING
        if conversation.provider_conversation_id
        else LearningConversation.CLEANUP_NOT_REQUIRED
    )
    conversation.save(
        update_fields=[
            "lifecycle",
            "active_scope_key",
            "tombstoned_at",
            "retention_expires_at",
            "cleanup_lease_token",
            "cleanup_lease_expires_at",
            "cleanup_next_attempt_at",
            "provider_cleanup_status",
            "updated_at",
        ]
    )
    # Bounded contexts react synchronously inside this transaction. If any
    # invariant reaction fails, clearing the conversation rolls back as well.
    conversation_fenced.send(
        sender=LearningConversation,
        conversation_key=conversation.conversation_key,
    )
    # Immediate deletion closes the cache race even inside a surrounding
    # transaction (for example a request-level atomic block); on_commit repeats
    # it after durable DB fencing.
    _delete_stream_cache(job_ids)
    transaction.on_commit(lambda ids=job_ids: _delete_stream_cache(ids))
    if conversation.provider_conversation_id:
        transaction.on_commit(_dispatch_cleanup_reconcile)
    return int(deleted_history_count), int(deleted_job_count), job_ids


def _owned_scope(username: str, learning_goal_id: int, *, lock: bool = False):
    user_qs = UserProfile.objects
    goal_qs = LearningGoal.objects
    if lock:
        user_qs = user_qs.select_for_update()
        goal_qs = goal_qs.select_for_update()
    user = user_qs.filter(username=str(username or "")).first()
    goal = goal_qs.filter(id=int(learning_goal_id), user=user).first() if user else None
    if not user or not goal:
        raise ConversationScopeError("conversation_scope_not_found")
    return user, goal


def get_active_conversation(username: str, learning_goal_id: int) -> Optional[LearningConversation]:
    """Return the one active local generation for an owned goal."""

    now = timezone.now()
    return (
        LearningConversation.objects.filter(
            user__username=str(username or ""),
            learning_goal_id=int(learning_goal_id),
            lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
            active_scope_key__isnull=False,
        )
        .filter(retention_expires_at__gt=now)
        .order_by("-generation")
        .first()
    )


def ensure_active_conversation(username: str, learning_goal_id: int) -> LearningConversation:
    """Get or create the active generation under a goal-row transaction lock."""

    created = False
    try:
        with transaction.atomic():
            user, goal = _owned_scope(username, learning_goal_id, lock=True)
            fixed_now = timezone.now()
            existing = (
                LearningConversation.objects.select_for_update()
                .filter(
                    user=user,
                    learning_goal=goal,
                    lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
                    active_scope_key__isnull=False,
                )
                .order_by("-generation")
                .first()
            )
            if existing and existing.retention_expires_at and existing.retention_expires_at > fixed_now:
                return existing
            if existing:
                _scrub_and_tombstone_locked(existing, goal, fixed_now=fixed_now)
            last_generation = (
                LearningConversation.objects.filter(user=user, learning_goal=goal)
                .aggregate(value=Max("generation"))
                .get("value")
                or 0
            )
            conversation = LearningConversation.objects.create(
                conversation_key=_local_conversation_key(),
                user=user,
                learning_goal=goal,
                generation=int(last_generation) + 1,
                active_scope_key=_active_scope_key(user.user_id, goal.id),
                lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
                provider_cleanup_status=LearningConversation.CLEANUP_NOT_REQUIRED,
                policy_version=CONVERSATION_POLICY_VERSION,
                retention_expires_at=fixed_now + timedelta(days=_retention_days()),
                mastery_write_authorized=False,
            )
            created = True
    except IntegrityError:
        conversation = get_active_conversation(username, learning_goal_id)
        if not conversation:
            raise

    # Optional mirror creation is deliberately outside the correctness
    # transaction. Failure leaves a fully usable local conversation.
    if created and bool(getattr(settings, "LEARNING_REMOTE_CONVERSATION_MIRROR_ENABLED", False)):
        provider_id = conversation_provider_service.create_remote_conversation(
            {"local_generation": str(conversation.generation)}
        )
        if provider_id:
            LearningConversation.objects.filter(
                conversation_key=conversation.conversation_key,
                lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
                provider_conversation_id__isnull=True,
            ).update(provider_conversation_id=provider_id)
            conversation.refresh_from_db()
    return conversation


def ensure_active_conversation_key(username: str, learning_goal_id: int) -> str:
    return ensure_active_conversation(username, learning_goal_id).conversation_key


def is_conversation_writable(
    username: str,
    learning_goal_id: int,
    conversation_key: str,
    *,
    lock: bool = False,
) -> bool:
    if not conversation_key:
        return False
    qs = LearningConversation.objects
    if lock:
        qs = qs.select_for_update()
    now = timezone.now()
    return qs.filter(
        conversation_key=str(conversation_key),
        user__username=str(username or ""),
        learning_goal_id=int(learning_goal_id),
        lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
        active_scope_key__isnull=False,
    ).filter(retention_expires_at__gt=now).exists()


def require_writable_conversation(
    username: str,
    learning_goal_id: int,
    conversation_key: str,
    *,
    lock: bool = False,
) -> LearningConversation:
    qs = LearningConversation.objects
    if lock:
        qs = qs.select_for_update()
    now = timezone.now()
    conversation = qs.filter(
        conversation_key=str(conversation_key or ""),
        user__username=str(username or ""),
        learning_goal_id=int(learning_goal_id),
        lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
        active_scope_key__isnull=False,
    ).filter(retention_expires_at__gt=now).first()
    if not conversation:
        raise ConversationFencedError("conversation_generation_fenced")
    return conversation


def touch_conversation_activity(
    username: str,
    learning_goal_id: int,
    conversation_key: str,
    *,
    now=None,
) -> bool:
    """Sliding retention renewal, fenced to the captured generation."""

    fixed_now = now or timezone.now()
    with transaction.atomic():
        conversation = require_writable_conversation(
            username,
            learning_goal_id,
            conversation_key,
            lock=True,
        )
        conversation.last_activity_at = fixed_now
        conversation.retention_expires_at = fixed_now + timedelta(days=_retention_days())
        conversation.save(update_fields=["last_activity_at", "retention_expires_at", "updated_at"])
    return True


def run_if_active(
    username: str,
    learning_goal_id: int,
    conversation_key: str,
    callback,
):
    """Run an arbitrary local mutation under the captured generation lock."""

    with transaction.atomic():
        require_writable_conversation(
            username,
            learning_goal_id,
            conversation_key,
            lock=True,
        )
        return callback()


def visible_history_queryset() -> QuerySet[UserHistory]:
    """The only production history base: active, locally scoped generations."""

    now = timezone.now()
    return (
        UserHistory.objects.filter(
            scope_status=UserHistory.SCOPE_ACTIVE,
            conversation__isnull=False,
            conversation__lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
            conversation__active_scope_key__isnull=False,
        )
        .filter(conversation__retention_expires_at__gt=now)
    )


def clear_conversation(username: str, learning_goal_id: int, *, now=None) -> ConversationClearResult:
    """Tombstone one generation and physically remove all generation data."""

    fixed_now = now or timezone.now()
    with transaction.atomic():
        user, goal = _owned_scope(username, learning_goal_id, lock=True)
        conversation = (
            LearningConversation.objects.select_for_update()
            .filter(
                user=user,
                learning_goal=goal,
                lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
                active_scope_key__isnull=False,
            )
            .order_by("-generation")
            .first()
        )
        if not conversation:
            return ConversationClearResult(cleared=False)

        deleted_history_count, deleted_job_count, _job_ids = _scrub_and_tombstone_locked(
            conversation,
            goal,
            fixed_now=fixed_now,
        )
    return ConversationClearResult(
        cleared=True,
        conversation_key=conversation.conversation_key,
        generation=conversation.generation,
        next_generation=conversation.generation + 1,
        deleted_history_count=int(deleted_history_count),
        deleted_job_count=int(deleted_job_count),
    )


def _cleanup_http_status(provider_conversation_id: str) -> int:
    return int(conversation_provider_service.delete_remote_conversation(provider_conversation_id))


def reconcile_provider_cleanup(*, limit: int = 100, now=None) -> dict:
    """Lease and reconcile due provider mirrors; 404 is terminal success."""

    fixed_now = now or timezone.now()
    candidates = list(
        LearningConversation.objects.filter(
            lifecycle=LearningConversation.LIFECYCLE_TOMBSTONED,
            provider_cleanup_status__in=[
                LearningConversation.CLEANUP_PENDING,
                LearningConversation.CLEANUP_RETRYING,
            ],
            provider_conversation_id__isnull=False,
        )
        .filter(Q(cleanup_next_attempt_at__isnull=True) | Q(cleanup_next_attempt_at__lte=fixed_now))
        .filter(Q(cleanup_lease_expires_at__isnull=True) | Q(cleanup_lease_expires_at__lte=fixed_now))
        .order_by("cleanup_next_attempt_at", "created_at")
        .values_list("conversation_key", flat=True)[: max(1, int(limit))]
    )
    succeeded = retried = skipped = 0
    for conversation_key in candidates:
        lease_token = uuid.uuid4().hex
        with transaction.atomic():
            conversation = LearningConversation.objects.select_for_update().filter(
                conversation_key=conversation_key
            ).first()
            if (
                not conversation
                or conversation.lifecycle != LearningConversation.LIFECYCLE_TOMBSTONED
                or conversation.provider_cleanup_status
                not in {LearningConversation.CLEANUP_PENDING, LearningConversation.CLEANUP_RETRYING}
                or not conversation.provider_conversation_id
                or (conversation.cleanup_lease_expires_at and conversation.cleanup_lease_expires_at > fixed_now)
            ):
                skipped += 1
                continue
            provider_id = conversation.provider_conversation_id
            conversation.cleanup_lease_token = lease_token
            conversation.cleanup_lease_expires_at = fixed_now + timedelta(seconds=CLEANUP_LEASE_SECONDS)
            conversation.save(update_fields=["cleanup_lease_token", "cleanup_lease_expires_at", "updated_at"])

        try:
            status_code = _cleanup_http_status(provider_id)
        except Exception as exc:  # network/provider failures are retryable
            logger.warning("Conversation provider cleanup failed for %s: %s", conversation_key, exc.__class__.__name__)
            status_code = 0

        with transaction.atomic():
            conversation = LearningConversation.objects.select_for_update().filter(
                conversation_key=conversation_key,
                cleanup_lease_token=lease_token,
            ).first()
            if not conversation:
                skipped += 1
                continue
            conversation.provider_cleanup_attempts += 1
            conversation.cleanup_lease_token = ""
            conversation.cleanup_lease_expires_at = None
            if status_code == 404 or 200 <= status_code < 300:
                conversation.provider_cleanup_status = LearningConversation.CLEANUP_SUCCEEDED
                conversation.provider_conversation_id = None
                conversation.provider_cleanup_error = ""
                conversation.cleanup_next_attempt_at = None
                succeeded += 1
            else:
                conversation.provider_cleanup_status = LearningConversation.CLEANUP_RETRYING
                conversation.provider_cleanup_error = (
                    f"provider_http_{status_code}" if status_code else "provider_unavailable"
                )
                delay = min(24 * 3600, 2 ** min(conversation.provider_cleanup_attempts, 12) * 30)
                conversation.cleanup_next_attempt_at = fixed_now + timedelta(seconds=delay)
                retried += 1
            conversation.save(
                update_fields=[
                    "provider_cleanup_attempts",
                    "provider_cleanup_status",
                    "provider_conversation_id",
                    "provider_cleanup_error",
                    "cleanup_next_attempt_at",
                    "cleanup_lease_token",
                    "cleanup_lease_expires_at",
                    "updated_at",
                ]
            )
    return {"scanned": len(candidates), "succeeded": succeeded, "retried": retried, "skipped": skipped}


def purge_expired_conversations(*, limit: int = 500, now=None) -> int:
    """Scrub retained tombstones only after provider cleanup is terminal."""

    fixed_now = now or timezone.now()
    keys = list(
        LearningConversation.objects.filter(
            lifecycle=LearningConversation.LIFECYCLE_TOMBSTONED,
            retention_expires_at__lte=fixed_now,
            provider_cleanup_status__in=[
                LearningConversation.CLEANUP_NOT_REQUIRED,
                LearningConversation.CLEANUP_SUCCEEDED,
            ],
            provider_conversation_id__isnull=True,
        )
        .order_by("retention_expires_at")
        .values_list("conversation_key", flat=True)[: max(1, int(limit))]
    )
    if not keys:
        return 0
    return LearningConversation.objects.filter(conversation_key__in=keys).update(
        lifecycle=LearningConversation.LIFECYCLE_DELETED,
        provider_cleanup_error="",
        cleanup_lease_token="",
        cleanup_lease_expires_at=None,
        cleanup_next_attempt_at=None,
        deleted_at=fixed_now,
    )


def expire_active_conversations(*, limit: int = 100, now=None) -> int:
    """Fence and scrub active generations whose retention deadline passed."""

    fixed_now = now or timezone.now()
    keys = list(
        LearningConversation.objects.filter(
            lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
            active_scope_key__isnull=False,
        )
        .filter(Q(retention_expires_at__isnull=True) | Q(retention_expires_at__lte=fixed_now))
        .order_by("retention_expires_at", "created_at")
        .values_list("conversation_key", flat=True)[: max(1, int(limit))]
    )
    expired = 0
    for key in keys:
        with transaction.atomic():
            candidate = LearningConversation.objects.filter(conversation_key=key).values(
                "learning_goal_id"
            ).first()
            if not candidate:
                continue
            goal = LearningGoal.objects.select_for_update().filter(
                id=candidate["learning_goal_id"]
            ).first()
            conversation = LearningConversation.objects.select_for_update().filter(
                conversation_key=key,
                lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
                active_scope_key__isnull=False,
            ).first()
            if (
                not goal
                or not conversation
                or (
                    conversation.retention_expires_at
                    and conversation.retention_expires_at > fixed_now
                )
            ):
                continue
            _scrub_and_tombstone_locked(conversation, goal, fixed_now=fixed_now)
            expired += 1
    return expired


def reconcile_conversation_cleanup(*, cleanup_limit: int = 100, retention_limit: int = 500, now=None) -> dict:
    fixed_now = now or timezone.now()
    expired_active = expire_active_conversations(limit=cleanup_limit, now=fixed_now)
    provider = reconcile_provider_cleanup(limit=cleanup_limit, now=fixed_now)
    provider["expired_active"] = expired_active
    provider["retention_deleted"] = purge_expired_conversations(limit=retention_limit, now=fixed_now)
    return provider


__all__ = [
    "CONVERSATION_POLICY_VERSION",
    "ConversationClearResult",
    "ConversationFencedError",
    "ConversationScopeError",
    "clear_conversation",
    "ensure_active_conversation",
    "ensure_active_conversation_key",
    "expire_active_conversations",
    "get_active_conversation",
    "is_conversation_writable",
    "purge_expired_conversations",
    "reconcile_conversation_cleanup",
    "reconcile_provider_cleanup",
    "require_writable_conversation",
    "run_if_active",
    "touch_conversation_activity",
    "visible_history_queryset",
]
