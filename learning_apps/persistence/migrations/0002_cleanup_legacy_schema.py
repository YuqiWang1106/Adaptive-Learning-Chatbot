from django.db import migrations


DEPRECATED_VIEWS = (
    "leaderboard",
    "user_stats",
    "user_history_view",
)

DEPRECATED_TABLES = (
    "recall_analysis",
    "fact_units",
    "qa_turns",
    "user_bookmarks",
    "user_pending_remediation",
)

DEPRECATED_COLUMNS = {
    "users": (
        "target_fact_count",
        "raw_self_assessment",
        "evaluation_report",
    ),
    "user_histories": (
        "recall_submitted",
        "recall_timestamp",
        "recall_score",
        "facts_hit",
        "facts_miss",
        "facts_wrong",
        "facts_added_true",
        "facts_added_false",
        "recall_corrections",
        "target_fact_count",
    ),
    "self_assessments": (
        "problem",
        "facts_examples",
        "facts_uncertainties",
        "strategies_examples",
        "strategies_uncertainties",
        "procedures_examples",
        "procedures_uncertainties",
        "rationales_examples",
        "rationales_uncertainties",
    ),
}


def _table_exists(connection, cursor, table_name: str) -> bool:
    """Internal helper to handle table exists."""
    if connection.vendor != "mysql":
        return table_name in connection.introspection.table_names(cursor)
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        """,
        (table_name,),
    )
    return (cursor.fetchone() or [0])[0] > 0


def _column_exists(connection, cursor, table_name: str, column_name: str) -> bool:
    """Internal helper to handle column exists."""
    if connection.vendor != "mysql":
        try:
            columns = connection.introspection.get_table_description(cursor, table_name)
        except Exception:
            return False
        return column_name in {column.name for column in columns}
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
        """,
        (table_name, column_name),
    )
    return (cursor.fetchone() or [0])[0] > 0


def cleanup_legacy_schema(apps, schema_editor):
    """Handle cleanup legacy schema."""
    del apps
    connection = schema_editor.connection
    quote_name = schema_editor.quote_name
    with connection.cursor() as cursor:
        for view_name in DEPRECATED_VIEWS:
            cursor.execute(f"DROP VIEW IF EXISTS {quote_name(view_name)}")

        for table_name in DEPRECATED_TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {quote_name(table_name)}")

        for table_name, columns in DEPRECATED_COLUMNS.items():
            if not _table_exists(connection, cursor, table_name):
                continue
            for column_name in columns:
                if _column_exists(connection, cursor, table_name, column_name):
                    cursor.execute(f"ALTER TABLE {quote_name(table_name)} DROP COLUMN {quote_name(column_name)}")


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(cleanup_legacy_schema, reverse_code=migrations.RunPython.noop),
    ]
