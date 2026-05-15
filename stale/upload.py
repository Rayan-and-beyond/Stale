"""Files API helpers — upload extracted curriculum text and produce session
resource entries that mount each course at `/workspace/curriculum/<safe>.txt`.

Why one .txt per course (not per slide deck):
    Sessions cap mount count and per-call resource overhead. One file per
    course keeps the agent's `glob /workspace/curriculum/*.txt` clean and
    predictable, while preserving deck/slide structure inside the file via
    `=== FILE: ===` and `--- Slide N ---` markers.
"""

from __future__ import annotations

import re
from typing import Any

import anthropic

CURRICULUM_MOUNT_PREFIX = "/workspace/curriculum"
DATA_MOUNT_PREFIX = "/workspace/data"


def _safe_filename(name: str) -> str:
    """Sanitize a course name into a filesystem-safe stem.
    'Operating Systems' -> 'operating_systems'
    'Lang Theory & Finite Automata' -> 'lang_theory_and_finite_automata'
    """
    s = name.strip().lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "course"


def upload_curriculum(
    client: anthropic.Anthropic,
    courses: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Upload each course text as a .txt file. Returns a mapping:
        { course_name: { "file_id": ..., "filename": ..., "mount_path": ... } }
    """
    out: dict[str, dict[str, str]] = {}
    for course_name, text in courses.items():
        stem = _safe_filename(course_name)
        filename = f"{stem}.txt"
        mount_path = f"{CURRICULUM_MOUNT_PREFIX}/{filename}"
        meta = client.beta.files.upload(
            file=(filename, text.encode("utf-8"), "text/plain"),
        )
        out[course_name] = {
            "file_id": meta.id,
            "filename": filename,
            "mount_path": mount_path,
        }
    return out


def upload_data_file(
    client: anthropic.Anthropic,
    path: str,
    *,
    mount_filename: str | None = None,
    content_type: str = "application/json",
) -> dict[str, str]:
    """Upload a single auxiliary data file (e.g. job_postings.json) and return
    its file_id + intended mount path under /workspace/data/.
    """
    from pathlib import Path

    p = Path(path)
    name = mount_filename or p.name
    mount_path = f"{DATA_MOUNT_PREFIX}/{name}"
    with p.open("rb") as fh:
        meta = client.beta.files.upload(file=(name, fh.read(), content_type))
    return {"file_id": meta.id, "filename": name, "mount_path": mount_path}


def curriculum_resources(uploaded: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Convert the upload_curriculum() output into a `resources=` list for
    `client.beta.sessions.create()`."""
    return [
        {"type": "file", "file_id": v["file_id"], "mount_path": v["mount_path"]}
        for v in uploaded.values()
    ]


def data_resource(uploaded: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "file",
        "file_id": uploaded["file_id"],
        "mount_path": uploaded["mount_path"],
    }
