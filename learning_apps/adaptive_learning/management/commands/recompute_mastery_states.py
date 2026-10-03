from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from learning_apps.adaptive_learning.mastery_evidence_policy import MASTERY_EVIDENCE_POLICY_VERSION, evidence_is_admitted
from learning_apps.adaptive_learning.mastery_service import recompute_mastery_state
from learning_apps.persistence.models import AdaptiveInteractionEvent, LearnerMasteryState, UserProfile


class Command(BaseCommand):
    help = "Recompute concept-scoped mastery states from admitted evidence (P1.3)."

    def add_arguments(self, parser):
        parser.add_argument("--user", dest="username", default="", help="Limit to a learner username.")
        parser.add_argument("--learning-goal", dest="goal_id", type=int, default=0, help="Limit to a learning goal id.")
        parser.add_argument("--concept-key", dest="concept_key", default="", help="Limit to one canonical concept key.")
        parser.add_argument("--limit", type=int, default=0, help="Maximum number of states to recompute (0 means all).")
        parser.add_argument("--dry-run", action="store_true", help="Report eligible states without writing changes.")
        parser.add_argument(
            "--policy-version",
            default=MASTERY_EVIDENCE_POLICY_VERSION,
            help="Policy version to apply; unknown versions fail closed.",
        )

    def handle(self, *args, **options):
        policy_version = str(options["policy_version"] or "")
        if policy_version != MASTERY_EVIDENCE_POLICY_VERSION:
            raise CommandError(
                f"Unsupported policy version {policy_version!r}; expected {MASTERY_EVIDENCE_POLICY_VERSION!r}."
            )
        username = str(options["username"] or "").strip()
        user = UserProfile.objects.filter(username=username).first() if username else None
        if username and user is None:
            raise CommandError(f"Unknown user: {username}")
        states = LearnerMasteryState.objects.select_related("user", "learning_goal").order_by("id")
        if user:
            states = states.filter(user=user)
        if options["goal_id"]:
            states = states.filter(learning_goal_id=int(options["goal_id"]))
        if options["concept_key"]:
            states = states.filter(concept_key=str(options["concept_key"]).strip())
        if options["limit"]:
            states = states[: max(0, int(options["limit"]))]

        inspected = recomputed = skipped = 0
        for state in states:
            inspected += 1
            current_event = next(
                (
                    event
                    for event in AdaptiveInteractionEvent.objects.filter(
                        user=state.user,
                        learning_goal=state.learning_goal,
                        concept_key=state.concept_key,
                    ).order_by("-created_at", "-id")
                    if evidence_is_admitted(event.metadata)
                ),
                None,
            )
            if current_event is None:
                skipped += 1
                continue
            if not options["dry_run"]:
                with transaction.atomic():
                    recompute_mastery_state(
                        user=state.user,
                        goal=state.learning_goal,
                        concept_key=state.concept_key,
                        current_event=current_event,
                    )
            recomputed += 1

        mode = "dry-run" if options["dry_run"] else "write"
        self.stdout.write(
            self.style.SUCCESS(
                f"P1.3 mastery recompute ({mode}) policy={policy_version}: "
                f"inspected={inspected} recomputed={recomputed} skipped={skipped}"
            )
        )
