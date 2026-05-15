"""Regression scan: ensure no real-looking secrets ship in the repo.

Targeted at:
  - Google API keys (AIza-prefixed, 20+ continuation chars)
  - Anthropic API keys (sk-ant- prefix, real token shape)
  - AWS access keys (AKIA[0-9A-Z]{16})

Scans every checked-in source/data/output file. Templates that intentionally
show key *shapes* (e.g. `.env.example` with `sk-ant-...`) are exempt via
allowlist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Secret-shape patterns. AIza requires 20+ continuation chars (real Google
# keys are 35); sk-ant requires a hyphen separator + token chunk; AKIA needs
# the canonical 16 uppercase-alphanumeric tail.
PATTERNS: dict[str, re.Pattern[str]] = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    "anthropic_api_key": re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
}

# Files that legitimately contain secret-shaped placeholders.
ALLOWLIST: set[Path] = {
    REPO / ".env.example",                          # `sk-ant-...` placeholder
    REPO / "tests" / "test_no_secrets_shipped.py",  # this file's own patterns
}

# Directory roots to scan. We deliberately skip output/auditor/, .venv/, etc.
SCAN_ROOTS: list[Path] = [
    REPO / "stale",
    REPO / "data",
    REPO / "docs",
    REPO / "tests",
    REPO / "output" / "runs",
    REPO / "README.md",
    REPO / "STATE.md",
    REPO / "LICENSE",
    REPO / "pyproject.toml",
    REPO / ".gitignore",
    REPO / ".env.example",
]

# File suffixes worth scanning. Binary / generated assets are skipped.
TEXT_SUFFIXES = {
    ".py", ".md", ".json", ".html", ".css", ".js", ".txt", ".toml",
    ".yaml", ".yml", ".cfg", ".ini", ".example", "",
}


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            out.append(root)
        elif root.is_dir():
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if "__pycache__" in p.parts:
                    continue
                if p.suffix in TEXT_SUFFIXES or p.suffix == "":
                    out.append(p)
    return out


def test_no_secret_shaped_strings_shipped():
    leaks: list[str] = []
    for path in _iter_files():
        if path in ALLOWLIST:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in PATTERNS.items():
            for match in pattern.findall(text):
                leaks.append(f"{path.relative_to(REPO)}: {name} matched {match!r}")
    assert not leaks, (
        "Secret-shaped strings detected in shipped files. Redact before "
        "committing.\n  " + "\n  ".join(leaks)
    )


def test_shipped_session_metadata_has_no_account_linked_ids():
    """Demo run metadata should not expose Anthropic account-linked IDs.

    Skips runs whose `meta._source_run` is set — those are user-generated
    retests (local working data, not curated for shipping). Curate them
    explicitly (strip IDs, drop `_source_run` from meta) before they
    qualify for the no-leak scan."""
    forbidden_keys = {"session_id", "agent_id", "environment_id", "file_id"}
    leaks: list[str] = []

    for path in (REPO / "output" / "runs").rglob("session_meta.json"):
        # session_meta.json lives at <run>/<agent>/session_meta.json
        run_dir = path.parent.parent
        meta_path = run_dir / "meta.json"
        if meta_path.exists():
            try:
                if json.loads(meta_path.read_text()).get("_source_run"):
                    continue
            except json.JSONDecodeError:
                pass
        payload = json.loads(path.read_text())

        def walk(value, trail: str = ""):
            if isinstance(value, dict):
                for key, child in value.items():
                    child_trail = f"{trail}.{key}" if trail else key
                    if key in forbidden_keys:
                        leaks.append(f"{path.relative_to(REPO)}: {child_trail}")
                    walk(child, child_trail)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{trail}[{index}]")

        walk(payload)

    assert not leaks, (
        "Account-linked Anthropic IDs found in shipped session metadata. "
        "Strip them from curated output before committing.\n  "
        + "\n  ".join(leaks)
    )
