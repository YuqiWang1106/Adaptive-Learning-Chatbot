from django.db import migrations, models


GUIDE_VERSION = "csa-self-assessment-guide-v2"
PROMPT_VERSION = "legacy-example-text-migration-v1"
DIMENSION_KEYS = ("facts", "strategies", "procedures", "rationales")
FALLBACK_DIMENSIONS = {
    "facts": (
        "State a relevant term, definition, example, or relationship you know. "
        "Be specific, and name what remains uncertain."
    ),
    "strategies": (
        "Describe an approach you could choose, when it is useful, and the "
        "decision point you are still unsure about."
    ),
    "procedures": (
        "Describe the ordered actions you could carry out and how you would "
        "check the result. Identify any unreliable step."
    ),
    "rationales": (
        "Explain why an idea, choice, or step works by connecting it to a "
        "principle or cause-and-effect relationship."
    ),
}


def _artifact_from_legacy_text(value):
    paragraphs = [
        " ".join(block.split())
        for block in str(value or "").split("\n\n")
        if block.strip()
    ]
    context = (
        paragraphs[0]
        if len(paragraphs) == 5
        else "Use a nearby task, exam, or project as a boundary for reflection."
    )
    dimensions = {}
    for index, key in enumerate(DIMENSION_KEYS, start=1):
        guidance = paragraphs[index] if len(paragraphs) == 5 else FALLBACK_DIMENSIONS[key]
        dimensions[key] = {"guidance": guidance}
    return {
        "version": GUIDE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "response_language": "English",
        "adjacent_topic": "Migrated nearby-topic guide",
        "context": context,
        "dimensions": dimensions,
        "source": "legacy_migration",
        "model_configuration": {},
    }


def migrate_example_text(apps, schema_editor):
    LearningGoal = apps.get_model("main", "LearningGoal")
    for goal in LearningGoal.objects.all().iterator():
        goal.self_assessment_guides = _artifact_from_legacy_text(goal.example_text)
        snapshot = dict(goal.goal_snapshot or {})
        snapshot.pop("example_text", None)
        snapshot["self_assessment_guides_version"] = GUIDE_VERSION
        goal.goal_snapshot = snapshot
        goal.save(update_fields=["self_assessment_guides", "goal_snapshot"])


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0039_normalize_self_assessment_statements"),
    ]

    operations = [
        migrations.AddField(
            model_name="learninggoal",
            name="self_assessment_guides",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(migrate_example_text, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="learninggoal",
            name="example_text",
        ),
        migrations.DeleteModel(
            name="KnowledgeBaseEntry",
        ),
    ]
