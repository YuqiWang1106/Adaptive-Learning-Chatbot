from django.db import migrations


DIMENSIONS = ("facts", "strategies", "procedures", "rationales")


def _text(value):
    return str(value or "").strip()


def normalize_submission_snapshots(apps, schema_editor):
    del schema_editor
    SelfAssessment = apps.get_model("main", "SelfAssessment")
    for assessment in SelfAssessment.objects.all().iterator(chunk_size=500):
        snapshot = assessment.submission_snapshot
        if not isinstance(snapshot, dict):
            continue
        dimensions = snapshot.get("dimensions")
        if not isinstance(dimensions, dict):
            continue

        normalized = {}
        for dimension in DIMENSIONS:
            item = dimensions.get(dimension)
            if not isinstance(item, dict):
                item = {}
            state = _text(item.get("state"))
            statement = _text(
                item.get("statement")
                or item.get("examples")
                or item.get("state_statement")
            )
            if state == "not_started" and not statement:
                statement = "I have not learned this yet."
            normalized[dimension] = {
                "state": state,
                "statement": statement,
                "uncertainties": _text(item.get("uncertainties")),
            }

        updated_snapshot = dict(snapshot)
        updated_snapshot["version"] = "self_assessment_v2_statement_contract"
        updated_snapshot["dimensions"] = normalized
        student_text = (
            str(assessment.student_text or "")
            .replace("Student statement:", "Statement:")
            .replace("\nExamples:", "\nStatement:")
            .replace("  - Example:", "  - Statement:")
        )
        SelfAssessment.objects.filter(pk=assessment.pk).update(
            submission_snapshot=updated_snapshot,
            student_text=student_text,
        )


def restore_legacy_snapshot_keys(apps, schema_editor):
    del schema_editor
    SelfAssessment = apps.get_model("main", "SelfAssessment")
    for assessment in SelfAssessment.objects.all().iterator(chunk_size=500):
        snapshot = assessment.submission_snapshot
        if not isinstance(snapshot, dict):
            continue
        dimensions = snapshot.get("dimensions")
        if not isinstance(dimensions, dict):
            continue

        restored = {}
        for dimension in DIMENSIONS:
            item = dimensions.get(dimension)
            if not isinstance(item, dict):
                item = {}
            state = _text(item.get("state"))
            statement = _text(item.get("statement"))
            restored[dimension] = {
                "label": dimension.title(),
                "state": state,
                "state_statement": statement if state == "not_started" else "",
                "examples": "" if state == "not_started" else statement,
                "uncertainties": _text(item.get("uncertainties")),
            }

        updated_snapshot = dict(snapshot)
        updated_snapshot["version"] = "self_assessment_v2_target_scope"
        updated_snapshot["dimensions"] = restored
        SelfAssessment.objects.filter(pk=assessment.pk).update(
            submission_snapshot=updated_snapshot
        )


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0038_target_task_scope_contract"),
    ]

    operations = [
        migrations.RunPython(
            normalize_submission_snapshots,
            restore_legacy_snapshot_keys,
        ),
    ]
