from scripts.quality.check_tracked_secrets import find_secrets_in_text


def test_tracked_secret_scan_flags_literal_values_without_echoing_them() -> None:
    sample = "DB_" + "PASSWORD=" + "a-real-password\n"
    findings = find_secrets_in_text("setup.md", sample)

    assert [(item.path, item.line, item.rule) for item in findings] == [
        ("setup.md", 1, "db-password-assignment")
    ]


def test_tracked_secret_scan_allows_documented_placeholders() -> None:
    text = "\n".join(
        [
            "DB_PASSWORD=<your-local-database-password>",
            "- **Password:** `[REDACTED]`",
            "SECRET_KEY = os.getenv('SECRET_KEY', 'unsafe-dev-key-change-me')",
        ]
    )

    assert find_secrets_in_text("example.env", text) == []
