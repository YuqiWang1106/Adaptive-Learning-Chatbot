#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PLACEHOLDER_MARKERS = (
    "${",
    "<",
    ":latest",
    "[redacted]",
    "change-me",
    "changeme",
    "example",
    "os.getenv",
    "paste_",
    "placeholder",
    "retrieve-",
    "secret manager",
    "secret-manager",
    "test-",
    "your-",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


RULES = (
    (
        "db-password-assignment",
        re.compile(r"(?im)^[ \t]*(?:export[ \t]+)?DB_PASSWORD[ \t]*=[ \t]*([^\r\n#]+)$"),
    ),
    (
        "markdown-password",
        re.compile(r"(?im)^.*\*\*Password:\*\*[ \t]*`?([^`\r\n]+)`?[ \t]*$"),
    ),
    (
        "secret-key-fallback",
        re.compile(
            r"SECRET_KEY\s*=\s*(?:os\.environ\.get|os\.getenv)\([^,\n]+,\s*['\"]([^'\"]+)['\"]"
        ),
    ),
    (
        "openai-api-key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"`").lower()
    return not normalized or any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def find_secrets_in_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for rule, pattern in RULES:
        for match in pattern.finditer(text):
            candidate = match.group(1) if match.lastindex else match.group(0)
            if _is_placeholder(candidate):
                continue
            findings.append(Finding(path=path, line=text.count("\n", 0, match.start()) + 1, rule=rule))
    return findings


def _candidate_files(repo_root: Path) -> Iterable[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    for raw_path in completed.stdout.split(b"\0"):
        if raw_path:
            yield repo_root / raw_path.decode("utf-8", errors="surrogateescape")


def main() -> int:
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    findings: list[Finding] = []
    for path in _candidate_files(repo_root):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(find_secrets_in_text(str(path.relative_to(repo_root)), text))

    if findings:
        print("Repository secret scan failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line}: {finding.rule}")
        return 1

    print("Repository secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
