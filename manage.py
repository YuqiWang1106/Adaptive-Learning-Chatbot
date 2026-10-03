#!/usr/bin/env python3
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main() -> None:
    """Handle main."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_django.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Install dependencies before running manage.py."
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
