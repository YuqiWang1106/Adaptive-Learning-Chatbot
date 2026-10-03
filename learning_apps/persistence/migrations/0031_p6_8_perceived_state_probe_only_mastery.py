# P6.8 separates subjective self-assessment guidance from observed Probe-only
# mastery and rebuilds every derived state from admitted formal Probe evidence.

import math
from statistics import mean, pstdev

import django.db.models.deletion
from django.db import migrations, models


DIMENSIONS = ("facts", "procedures", "strategies", "rationales")
OLD_POLICY = "mastery_evidence_policy_v1"
NEW_POLICY = "mastery_evidence_policy_v2"
DIMENSION_WEIGHTS = {"facts": 0.35, "procedures": 0.30, "strategies": 0.25, "rationales": 0.10}
FUSION_WEIGHTS = {"local_recent": 0.50, "global_history": 0.25, "current_score": 0.15, "time_decay": 0.10}
CURVE_ALPHA = 0.45
CURVE_CONFIDENCE_THRESHOLD = 0.65


def _clamp(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if 1.0 < number <= 100.0:
        number /= 100.0
    return round(max(0.0, min(1.0, number)), 4)


def _normalize_scores(raw, fallback=0.5):
    values = raw if isinstance(raw, dict) else {}
    return {dimension: _clamp(values.get(dimension), fallback) for dimension in DIMENSIONS}


def _scores_from_report(structured_report):
    """Frozen P6.8 projection of a legacy structured self-assessment."""

    report = structured_report if isinstance(structured_report, dict) else {}
    label_scores = {
        "Know-Know": 0.88,
        "Know-Don't Know": 0.58,
        "Omission": 0.36,
        "False Knowledge": 0.22,
        "Irrelevant Knowledge": 0.31,
    }
    negative_labels = {"Omission", "False Knowledge", "Irrelevant Knowledge"}

    def labels(raw):
        raw = [raw] if isinstance(raw, str) else raw
        return [token for item in (raw or []) if isinstance(item, str) for token in (part.strip() for part in item.split(",")) if token]

    def optional_score(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if 1.0 < number <= 100.0:
            number /= 100.0
        return max(0.0, min(1.0, number))

    def explanation_modifier(text):
        lowered = str(text or "").lower()
        severe = any(token in lowered for token in ("did not provide any", "absence of any", "no evidence", "not correct", "not accurate", "not provided", "cannot evaluate", "missing"))
        bonus = 0.02 if not severe and any(token in lowered for token in ("accurate", "correct", "clear", "specific", "well explained", "strong")) else 0.0
        penalty = 0.04 if severe else 0.0
        uncertain = 0.02 if any(token in lowered for token in ("uncertain", "not sure", "unsure", "confused", "incomplete")) else 0.0
        return bonus - penalty - uncertain

    scores = {}
    for raw_name, dimension in (("Facts", "facts"), ("Strategies", "strategies"), ("Procedures", "procedures"), ("Rationales", "rationales")):
        parsed = report.get(raw_name) or report.get(dimension) or {}
        aspects = parsed.get("aspects") if isinstance(parsed, dict) else []
        values, weights = [], []
        for aspect in aspects or []:
            if not isinstance(aspect, dict):
                continue
            aspect_labels = labels(aspect.get("labels"))
            if not aspect_labels:
                continue
            expressed = {label for label in aspect_labels if label != "Omission"}
            if expressed and (str(aspect.get("basis") or "") not in {"student_text", "mixed"} or not str(aspect.get("student_quote") or "").strip()):
                values.append(0.0)
                weights.append(1.0)
                continue
            mapped = [label_scores[label] for label in aspect_labels if label in label_scores]
            if not mapped:
                continue
            confidence = optional_score(aspect.get("confidence"))
            multiplier = 1.0 if confidence is None else 0.96 + (0.08 * confidence)
            try:
                severity = max(1.0, min(5.0, float(aspect.get("severity"))))
            except (TypeError, ValueError):
                severity = 1.0
            modifier = explanation_modifier(f"{aspect.get('aspect', '')} {aspect.get('explanation', '')} {parsed.get('most_critical_gap', '')}")
            if any(label in negative_labels for label in aspect_labels):
                modifier -= ((severity - 1.0) / 4.0) * 0.08
            values.append(_clamp((mean(mapped) * multiplier) + modifier, 0.5))
            weights.append(optional_score(aspect.get("weight")) or 1.0)
        denominator = sum(weights) if sum(weights) > 0 else float(len(values) or 1)
        scores[dimension] = round(sum(value * weight for value, weight in zip(values, weights)) / denominator, 4) if values else 0.5
    return scores


def _average_scores(events, fallback):
    return {
        dimension: round(mean([_normalize_scores(event.dimension_scores, event.accuracy_score)[dimension] for event in events]), 4) if events else fallback[dimension]
        for dimension in DIMENSIONS
    }


def _dependency_adjusted(scores):
    adjusted = dict(scores)
    adjusted["procedures"] = min(adjusted["procedures"], adjusted["facts"] + 0.25)
    adjusted["strategies"] = min(adjusted["strategies"], mean([adjusted["facts"], adjusted["procedures"]]) + 0.25)
    adjusted["rationales"] = min(adjusted["rationales"], mean([adjusted["facts"], adjusted["procedures"], adjusted["strategies"]]) + 0.25)
    return {dimension: _clamp(value) for dimension, value in adjusted.items()}


def _linear_slope(values):
    if len(values) < 2:
        return 0.0
    x_mean = (len(values) - 1) / 2.0
    y_mean = mean(values)
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    return round(sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values)) / denominator, 4) if denominator else 0.0


def _curve_analysis(values, confidences):
    raw = [_clamp(value) for value in values[-100:]]
    if not raw:
        return {"pattern": "insufficient_data", "confidence": 0.0, "evidence_count": 0, "reason": {"reason": "no_curve_evidence", "values": []}}
    smooth = [raw[0]]
    for value in raw[1:]:
        smooth.append(round((CURVE_ALPHA * value) + ((1.0 - CURVE_ALPHA) * smooth[-1]), 4))
    deltas = [round(smooth[index] - smooth[index - 1], 4) for index in range(1, len(smooth))]
    raw_deltas = [round(raw[index] - raw[index - 1], 4) for index in range(1, len(raw))]
    slope, raw_slope = _linear_slope(smooth), _linear_slope(raw)
    total_delta = round(smooth[-1] - smooth[0], 4)
    recent_delta = deltas[-1] if deltas else 0.0
    volatility = round(pstdev(deltas), 4) if len(deltas) >= 2 else 0.0
    signs = [1 if delta > 0.015 else -1 if delta < -0.015 else 0 for delta in deltas]
    sign_changes = sum(1 for index in range(1, len(signs)) if signs[index] and signs[index - 1] and signs[index] != signs[index - 1])
    negative_steps = sum(1 for delta in deltas if delta <= -0.02)
    positive_steps = sum(1 for delta in deltas if delta >= 0.02)
    curve_range = round(max(smooth) - min(smooth), 4)
    raw_drawdown = round(max(raw[:-1], default=raw[-1]) - raw[-1], 4)
    raw_recent_delta = raw_deltas[-1] if raw_deltas else 0.0
    recent_range = round(max(smooth[-4:]) - min(smooth[-4:]), 4) if len(smooth) >= 4 else curve_range
    grader_confidence = round(mean([_clamp(value, 0.65) for value in confidences[-len(raw):]]), 4) if confidences else 0.65
    evidence_score = min(1.0, len(raw) / 6.0)
    stability_score = 1.0 - _clamp(volatility / 0.30)
    confidence = round((0.45 * evidence_score) + (0.30 * stability_score) + (0.25 * grader_confidence), 4)
    if len(raw) < 3:
        pattern, reason = "insufficient_data", "less_than_3_points"
    elif len(raw) < 5:
        pattern, reason = "developing", "3_or_4_points_no_strong_pattern"
    elif (raw_drawdown >= 0.20 and raw_recent_delta <= -0.08) or (raw_drawdown >= 0.35 and max(raw) - min(raw) >= 0.50):
        pattern, reason = "fluctuating", "large_recent_drawdown"
    elif (slope <= -0.045 or raw_slope <= -0.04) and total_delta <= -0.12 and negative_steps >= max(2, math.ceil(len(deltas) / 2)):
        pattern, reason = "declining", "negative_slope_and_repeated_declines"
    elif sign_changes >= 2 and volatility >= 0.10 and curve_range >= 0.20:
        pattern, reason = "fluctuating", "high_volatility_and_direction_changes"
    elif abs(slope) <= 0.015 and recent_range <= 0.08 and smooth[-1] < 0.75:
        pattern, reason = "plateau", "flat_low_mastery_window"
    elif slope >= 0.07 and total_delta >= 0.25 and recent_delta >= -0.03:
        pattern, reason = "fast_growth", "strong_positive_slope"
    elif slope >= 0.02 and total_delta >= 0.08:
        pattern, reason = "gradual_growth", "moderate_positive_slope"
    else:
        pattern, reason = "developing", "no_stable_pattern_yet"
    return {
        "pattern": pattern,
        "confidence": confidence,
        "evidence_count": len(raw),
        "reason": {
            "reason": reason, "values": raw, "smoothed_values": smooth, "slope": slope,
            "raw_slope": raw_slope, "total_delta": total_delta, "recent_delta": recent_delta,
            "volatility": volatility, "sign_changes": sign_changes, "positive_steps": positive_steps,
            "negative_steps": negative_steps, "range": curve_range,
            "drawdown": round(max(smooth[:-1], default=smooth[-1]) - smooth[-1], 4),
            "raw_drawdown": raw_drawdown, "mean_grader_confidence": grader_confidence,
            "evidence_score": round(evidence_score, 4), "stability_score": round(stability_score, 4),
        },
    }


def _feedback_tier(quality, pattern, curve_confidence):
    if curve_confidence >= CURVE_CONFIDENCE_THRESHOLD:
        if pattern == "declining":
            return "REVIEW"
        if pattern == "plateau":
            return "PLATEAU"
        if pattern == "fast_growth" and quality >= 0.70:
            return "REINFORCE"
        if pattern == "fluctuating":
            return "CONSOLIDATE"
    if quality >= 0.75:
        return "REINFORCE"
    if quality >= 0.45:
        return "CONSOLIDATE"
    return "SCAFFOLD"


def _response_policy(weakest, tier, pattern, curve_confidence, curve_reason):
    tier_policy = {
        "REINFORCE": "Confirm what is correct, then add a deeper challenge.",
        "CONSOLIDATE": "Acknowledge correct parts and repair the weakest relevant dimension.",
        "SCAFFOLD": "Reduce cognitive load and guide from fundamentals with short prompts.",
        "PLATEAU": "Change explanation mode using analogy, contrast, visual framing, or counterexample.",
        "REVIEW": "Review prerequisites and use spaced repetition before advancing.",
    }
    dimension_policy = {
        "facts": "Clarify definitions, vocabulary, and core examples before adding complexity.",
        "procedures": "Break the solution into small ordered steps and ask the learner to fill the missing operation.",
        "strategies": "Compare alternative methods and explain when each method should be used.",
        "rationales": "Ask why-questions and require the learner to explain the principle behind the method.",
    }
    labels = {"facts": "Facts", "procedures": "Procedures", "strategies": "Strategies", "rationales": "Rationales"}
    if curve_confidence < CURVE_CONFIDENCE_THRESHOLD:
        intervention = "Curve evidence is still low-confidence; adapt mainly from quality score and weakest dimension."
    elif pattern == "declining":
        intervention = "Lower difficulty, review prerequisites, and rebuild facts/procedures before advancing."
    elif pattern == "plateau":
        intervention = "Change explanation mode with analogy, visual framing, counterexample, or worked example."
    elif pattern == "fluctuating":
        intervention = "Use a short, low-ambiguity diagnostic question before increasing difficulty."
    elif pattern == "fast_growth":
        intervention = "Add a related transfer or challenge task while staying anchored to the current question."
    else:
        intervention = "Continue normal weakest-dimension support while collecting more trend evidence."
    return {
        "tier": tier,
        "tier_policy": tier_policy[tier],
        "weakest_dimension": weakest,
        "dimension_label": labels[weakest],
        "dimension_policy": dimension_policy[weakest],
        "curve_pattern": pattern,
        "curve_confidence": _clamp(curve_confidence),
        "curve_intervention": intervention,
        "curve_reason": curve_reason,
        "mastery_confidence_semantics": {
            "method": "heuristic_evidence_stability_v1",
            "calibration_status": "uncalibrated",
            "is_probability": False,
        },
    }


def _rebuild_probe_only_state(LearnerMasteryState, events):
    """Frozen policy-v2 event replay using historical ORM models only."""

    if not events:
        return
    previous = None
    for index, current in enumerate(events):
        current_events = events[: index + 1]
        current_scores = _normalize_scores(current.dimension_scores, current.accuracy_score)
        local_recent = _average_scores(current_events[-5:], current_scores)
        global_history = _average_scores(current_events, current_scores)
        if previous is not None:
            prior_event = current_events[-2]
            days = max(0.0, ((current.created_at - prior_event.created_at).total_seconds() / 86400.0))
            decay = math.exp(-days / 14.0)
            time_decay = {
                dimension: _clamp(getattr(previous, f"{dimension}_mastery") * decay)
                for dimension in DIMENSIONS
            }
        else:
            time_decay = current_scores
        fused = _dependency_adjusted({
            dimension: (
                FUSION_WEIGHTS["local_recent"] * local_recent[dimension]
                + FUSION_WEIGHTS["global_history"] * global_history[dimension]
                + FUSION_WEIGHTS["current_score"] * current_scores[dimension]
                + FUSION_WEIGHTS["time_decay"] * time_decay[dimension]
            )
            for dimension in DIMENSIONS
        })
        dimension_mastery = round(sum(fused[dimension] * DIMENSION_WEIGHTS[dimension] for dimension in DIMENSIONS), 4)
        quality = round((0.40 * _clamp(current.accuracy_score)) + (0.60 * dimension_mastery), 4)
        weakest = min(DIMENSIONS, key=lambda dimension: (fused[dimension], DIMENSIONS.index(dimension)))

        curve_by_dimension = {}
        for dimension in DIMENSIONS:
            values, confidences = [], []
            for event in current_events:
                metadata = event.metadata if isinstance(event.metadata, dict) else {}
                if event.id == current.id:
                    score = fused[dimension]
                else:
                    mastery_after = metadata.get("mastery_after") if isinstance(metadata.get("mastery_after"), dict) else {}
                    score = _normalize_scores(mastery_after or event.dimension_scores, event.accuracy_score)[dimension]
                values.append(score)
                confidences.append(_clamp(event.confidence, 0.65))
            curve_by_dimension[dimension] = _curve_analysis(values, confidences)
        selected_curve = curve_by_dimension[weakest]
        pattern = selected_curve["pattern"]
        curve_confidence = _clamp(selected_curve["confidence"])
        curve_reason = {
            "selected_dimension": weakest,
            "selected_pattern": pattern,
            "selected_confidence": curve_confidence,
            "selected_reason": selected_curve["reason"],
            "dimensions": {dimension: curve_by_dimension[dimension]["reason"] for dimension in DIMENSIONS},
        }
        tier = _feedback_tier(quality, pattern, curve_confidence)
        dimension_confidences = {}
        for dimension in DIMENSIONS:
            values = [_normalize_scores(event.dimension_scores, event.accuracy_score)[dimension] for event in current_events]
            confidences = [_clamp(event.confidence) for event in current_events]
            stability = 1.0 - _clamp(pstdev(values) / 0.35) if len(values) >= 2 else 0.0
            dimension_confidences[dimension] = round(
                (0.45 * min(1.0, len(values) / 5.0)) + (0.35 * mean(confidences)) + (0.20 * stability),
                4,
            )
        mastery_confidence = round(mean(dimension_confidences.values()), 4)
        source_summary = {
            "probe_response": {
                "count": len(current_events),
                "mean_confidence": round(mean([_clamp(event.confidence) for event in current_events]), 4),
                "last_event_id": current.id,
            }
        }
        previous, _created = LearnerMasteryState.objects.update_or_create(
            user_id=current.user_id,
            learning_goal_id=current.learning_goal_id,
            concept_key=current.concept_key,
            defaults={
                "facts_mastery": fused["facts"],
                "procedures_mastery": fused["procedures"],
                "strategies_mastery": fused["strategies"],
                "rationales_mastery": fused["rationales"],
                "dimension_mastery_score": dimension_mastery,
                "quality_score": quality,
                "weakest_dimension": weakest,
                "curve_pattern": pattern,
                "curve_confidence": curve_confidence,
                "curve_evidence_count": selected_curve["evidence_count"],
                "dimension_curve_patterns": {dimension: curve_by_dimension[dimension]["pattern"] for dimension in DIMENSIONS},
                "dimension_curve_confidences": {dimension: _clamp(curve_by_dimension[dimension]["confidence"]) for dimension in DIMENSIONS},
                "curve_reason": curve_reason,
                "feedback_tier": tier,
                "response_policy": _response_policy(weakest, tier, pattern, curve_confidence, curve_reason),
                "last_event_id": current.id,
                "last_eligible_evidence_id": current.id,
                "event_count": len(current_events),
                "eligible_evidence_count": len(current_events),
                "mastery_confidence": mastery_confidence,
                "dimension_confidences": dimension_confidences,
                "evidence_source_summary": source_summary,
                "policy_version": NEW_POLICY,
            },
        )
        metadata = dict(current.metadata) if isinstance(current.metadata, dict) else {}
        metadata.update({
            "mastery_after": fused,
            "dimension_mastery_score_after": dimension_mastery,
            "quality_score_after": quality,
            "weakest_dimension_after": weakest,
            "curve_pattern_after": pattern,
            "curve_confidence_after": curve_confidence,
            "feedback_tier_after": tier,
        })
        current.metadata = metadata
        current.save(update_fields=["metadata"])


def _band(score):
    if score < 0.45:
        return "reports_high_support_need"
    if score < 0.75:
        return "reports_moderate_support_need"
    return "reports_relative_confidence"


def separate_perceived_and_observed_state(apps, schema_editor):
    AdaptiveInteractionEvent = apps.get_model("main", "AdaptiveInteractionEvent")
    AdaptiveProbeOffer = apps.get_model("main", "AdaptiveProbeOffer")
    LearnerMasteryState = apps.get_model("main", "LearnerMasteryState")
    LearnerPerceivedState = apps.get_model("main", "LearnerPerceivedState")
    SelfAssessmentEvidenceDecision = apps.get_model("main", "SelfAssessmentEvidenceDecision")

    baseline_scores = {}
    for event in AdaptiveInteractionEvent.objects.filter(source="self_assessment_baseline").iterator():
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        assessment_id = metadata.get("self_assessment_id")
        if assessment_id:
            baseline_scores[int(assessment_id)] = _normalize_scores(
                event.dimension_scores,
                event.accuracy_score,
            )

    seen_goals = set()
    accepted = SelfAssessmentEvidenceDecision.objects.filter(
        status="accepted",
        invalidated_at__isnull=True,
        mastery_write_authorized=False,
    ).select_related("self_assessment").order_by(
        "user_id", "learning_goal_id", "-created_at", "-decision_id"
    )
    for decision in accepted.iterator():
        scope = (decision.user_id, decision.learning_goal_id)
        if scope in seen_goals:
            continue
        seen_goals.add(scope)
        assessment = decision.self_assessment
        scores = baseline_scores.get(assessment.id) or _scores_from_report(assessment.structured_report or {})
        dimension_scores = {
            dimension: round(float(scores.get(dimension, 0.0)), 4)
            for dimension in DIMENSIONS
        }
        LearnerPerceivedState.objects.update_or_create(
            learning_goal_id=decision.learning_goal_id,
            defaults={
                "user_id": decision.user_id,
                "source_assessment_id": decision.self_assessment_id,
                "evidence_decision_id": decision.pk,
                "dimension_scores": dimension_scores,
                "reported_uncertainties": {},
                "diagnostic_summary": {
                    dimension: {
                        "band": _band(dimension_scores[dimension]),
                        "reported_uncertainty": False,
                    }
                    for dimension in DIMENSIONS
                },
                "taxonomy_sha256": decision.taxonomy_sha256,
                "authority": "guidance_only",
                "projection_version": "perceived_state_v1",
                "mastery_write_authorized": False,
            },
        )

    # Preserve append-only historical baseline events for audit, but only
    # previously admitted formal Probe responses are upgraded into policy v2.
    derived_keys = {
        "mastery_after",
        "dimension_mastery_score_after",
        "quality_score_after",
        "weakest_dimension_after",
        "curve_pattern_after",
        "curve_confidence_after",
        "feedback_tier_after",
    }
    for event in AdaptiveInteractionEvent.objects.filter(source="probe_response").iterator():
        metadata = dict(event.metadata) if isinstance(event.metadata, dict) else {}
        if (
            metadata.get("mastery_evidence_admission") == "accepted"
            and metadata.get("mastery_update_accepted") is True
            and metadata.get("mastery_policy_version") == OLD_POLICY
        ):
            metadata["mastery_policy_version"] = NEW_POLICY
            metadata["p6_8_policy_upgraded"] = True
        # Old mastery_after values can contain self-assessment baseline
        # contributions. They must not enter the rebuilt Probe-only curve.
        for key in derived_keys:
            metadata.pop(key, None)
        event.metadata = metadata
        event.save(update_fields=["metadata"])

    # Derived mastery is disposable. Rebuild it exclusively from the current
    # Probe admission policy, leaving baseline-only goals with no observed row.
    LearnerMasteryState.objects.all().delete()
    admitted_probe_ids = []
    for event in AdaptiveInteractionEvent.objects.filter(source="probe_response").order_by(
        "user_id", "learning_goal_id", "concept_key", "created_at", "id"
    ).iterator():
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        if (
            metadata.get("mastery_evidence_admission") == "accepted"
            and metadata.get("mastery_policy_version") == NEW_POLICY
            and metadata.get("mastery_update_accepted") is True
        ):
            admitted_probe_ids.append(event.id)

    events_by_scope = {}
    for event in AdaptiveInteractionEvent.objects.filter(id__in=admitted_probe_ids).order_by(
        "user_id", "learning_goal_id", "concept_key", "created_at", "id"
    ):
        events_by_scope.setdefault(
            (event.user_id, event.learning_goal_id, event.concept_key), []
        ).append(event)
    for events in events_by_scope.values():
        _rebuild_probe_only_state(LearnerMasteryState, events)

    # Offers computed from the former baseline fingerprint must not survive.
    AdaptiveProbeOffer.objects.filter(
        source="review_scheduler",
        resulting_probe_id__isnull=True,
        status__in=["pending", "accepted", "generating", "snoozed"],
    ).update(
        status="superseded",
        open_scope_key=None,
        last_error_code="p6_8_mastery_rebuilt",
    )


def reverse_separation(apps, schema_editor):
    AdaptiveInteractionEvent = apps.get_model("main", "AdaptiveInteractionEvent")
    LearnerMasteryState = apps.get_model("main", "LearnerMasteryState")
    LearnerPerceivedState = apps.get_model("main", "LearnerPerceivedState")

    LearnerPerceivedState.objects.all().delete()
    LearnerMasteryState.objects.all().delete()
    for event in AdaptiveInteractionEvent.objects.filter(source="probe_response").iterator():
        metadata = dict(event.metadata) if isinstance(event.metadata, dict) else {}
        if metadata.pop("p6_8_policy_upgraded", False):
            metadata["mastery_policy_version"] = OLD_POLICY
            event.metadata = metadata
            event.save(update_fields=["metadata"])


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0030_p6_7_adaptive_context_micro_checks_probe_offers"),
    ]

    operations = [
        migrations.CreateModel(
            name="LearnerPerceivedState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("dimension_scores", models.JSONField(default=dict)),
                ("reported_uncertainties", models.JSONField(blank=True, default=dict)),
                ("diagnostic_summary", models.JSONField(blank=True, default=dict)),
                ("taxonomy_sha256", models.CharField(max_length=64)),
                ("authority", models.CharField(default="guidance_only", editable=False, max_length=32)),
                ("projection_version", models.CharField(default="perceived_state_v1", max_length=64)),
                ("mastery_write_authorized", models.BooleanField(default=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "evidence_decision",
                    models.ForeignKey(
                        db_column="evidence_decision_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="perceived_state_projections",
                        to="main.selfassessmentevidencedecision",
                    ),
                ),
                (
                    "learning_goal",
                    models.OneToOneField(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="perceived_state",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "source_assessment",
                    models.ForeignKey(
                        db_column="source_assessment_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="perceived_state_projections",
                        to="main.selfassessment",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="perceived_states",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={"db_table": "learner_perceived_states"},
        ),
        migrations.AddIndex(
            model_name="learnerperceivedstate",
            index=models.Index(fields=["user", "updated_at"], name="idx_lps_user_updated"),
        ),
        migrations.AddConstraint(
            model_name="learnerperceivedstate",
            constraint=models.CheckConstraint(
                check=models.Q(("authority", "guidance_only")),
                name="chk_lps_guidance_only",
            ),
        ),
        migrations.AddConstraint(
            model_name="learnerperceivedstate",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_lps_no_mastery",
            ),
        ),
        migrations.RunPython(separate_perceived_and_observed_state, reverse_separation),
    ]
