"""Market-fit session driver.

Pipeline:
  1. Extract curriculum from `curriculum/` (if not already extracted in this run).
  2. Upload each course's .txt + the role-tagged job-postings dataset via Files API.
  3. Create a session against STALE_MARKET_FIT_AGENT_ID with both files mounted.
  4. Stream-first: open SSE BEFORE sending the kickoff so we don't lose events.
  5. Send a kickoff message that fixes the target_role.
  6. Consume events until session.status_idle, parse the final JSON code block.

Outputs (under output_dir/):
  - events.jsonl, agent_text.md, session_meta.json
  - market_fit.json    — parsed JSON output

Usage:
    python -m stale.agents.market_fit --role backend [--course "Course Name"] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from stale.tools.extract import extract_curriculum
from stale.upload import (
    curriculum_resources,
    data_resource,
    upload_curriculum,
    upload_data_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_CURRICULUM = REPO_ROOT / "curriculum"
DEFAULT_POSTINGS = REPO_ROOT / "data" / "job_postings.json"
OUTPUT_ROOT = REPO_ROOT / "output" / "market_fit"

VALID_ROLES = {
    "backend", "frontend", "fullstack", "ml_data",
    "mobile", "devops_sre", "security", "embedded", "game",
}


def _kickoff_message(role: str, specialization: str | None = None,
                     scope: dict | None = None,
                     auditor_findings: list[dict] | None = None) -> str:
    scope_block = ""
    if scope:
        scope_block = (
            "\n## Course scope (auto-detected from slide content)\n\n"
            f"**Subject area:** {scope.get('subject_area', '')}\n"
            f"**Pedagogical level:** {scope.get('pedagogical_level', '')}\n"
            f"**Course intent:** {scope.get('course_intent', '')}\n"
            f"**Depth bound:** {scope.get('depth_bound', '')}\n"
            f"**Scope summary:** {scope.get('one_liner', '')}\n\n"
            "## Critical recommendation rules\n\n"
            "**Rule 1 — Extend partial coverage only.** Recommend extensions of "
            "topics the course already partially covers. Do NOT recommend topics "
            "the course covers 0% of, even if the topic is in the course's "
            "domain and the market demands it. Every gap you surface must cite "
            "a specific slide where the topic IS partially taught (use the "
            "slide's exact text). If no slide partially teaches the topic, do "
            "not include it in the gaps list. Recommendations are extensions "
            "of existing course content, not additions of new content. Aim to "
            "bring partial coverage up to a fuller treatment (e.g. 80–90%), "
            "not to perfection — overreach is worse than under-recommendation.\n\n"
            "**Rule 2 — Respect the depth bound.** Stay at the course's "
            "pedagogical level. For a broad introductory survey, recommend "
            "conceptual extensions of partially-covered topics — not "
            "implementation walkthroughs, framework tutorials, or theoretical "
            "deep dives, even if those would be in the same domain. For a "
            "hands-on lab, recommend implementation extensions — not abstract "
            "theory. The `depth_bound` above tells you the line.\n\n"
            "**Rule 3 — Cross-domain skills are out of scope (as before).** "
            "A skill from a different domain entirely (e.g. Docker in a SWE "
            "process course; OAuth in an OS course; Postgres in an AI survey) "
            "is not a gap regardless of market demand.\n\n"
            "**Worked discrimination examples** (use these as your reasoning "
            "template):\n\n"
            "- Course's existing NLP unit teaches phrase-structure grammars "
            "and parse trees → recommending a *conceptual* introduction to "
            "transformer architectures within that unit is fair (extending "
            "partially-covered topic, within survey depth). Recommending "
            "implementing attention math by hand or doing a tokenizer "
            "walkthrough is NOT — both exceed the depth bound.\n"
            "- Course names Python only on a job-description slide, never "
            "teaches Python syntax → recommending Python is NOT fair "
            "(0% coverage, no extension to make). Even if 100% of postings "
            "demand Python.\n"
            "- Course teaches knowledge representation via ontologies AND "
            "teaches retrieval/search separately → recommending a *conceptual* "
            "discussion of how these combine into RAG / vector databases IS "
            "fair (extends two partially-covered topics by connecting them, "
            "stays at the conceptual level).\n"
        )
    spec_block = ""
    if specialization:
        spec_block = (
            f"\nThe user has additionally indicated their specific focus is: "
            f"\"{specialization}\". You MUST still tally market demand against "
            f"ALL postings tagged role=={role!r} (not a sub-filter), but when "
            "deciding which gaps to surface and how to phrase rationales, "
            "weight your selection toward skills most relevant to this "
            "specialization where the data supports it. Do NOT invent gaps "
            "unsupported by the postings evidence — the specialization shapes "
            "emphasis, not facts.\n"
        )
    auditor_block = ""
    if auditor_findings:
        compact = [
            {
                "course": f.get("course"),
                "slide_ref": f.get("slide_ref"),
                "claim": f.get("claim"),
                "status": f.get("status"),
                "severity": f.get("severity"),
                "suggested_replacement": f.get("suggested_replacement"),
            }
            for f in auditor_findings
        ]
        auditor_block = (
            "\n## Auditor findings (deprecated / insecure / outdated patterns in this course)\n\n"
            "The Auditor agent flagged the patterns below as deprecated, "
            "insecure, or outdated. **When phrasing gap rationales and "
            "extension recommendations, never frame the extension as building "
            "on top of a flagged pattern.** If the natural extension of a "
            "partially-covered topic would land inside a flagged pattern, "
            "phrase the gap so that filling it requires *replacing* the "
            "flagged pattern with the modern alternative named in "
            "`suggested_replacement`.\n\n"
            "Concrete example: if Auditor flags `AsyncTask` as deprecated and "
            "the course partially covers REST consumption inside an AsyncTask "
            "lab, do NOT phrase the gap as \"extend AsyncTask-based REST "
            "calls.\" Phrase it as \"modernize the REST consumption lab off "
            "AsyncTask onto an Executor + Handler / coroutines.\"\n\n"
            "```json\n"
            + json.dumps(compact, indent=2)
            + "\n```\n"
        )
    return (
        f"Target role: {role}.{scope_block}{auditor_block}{spec_block}\n\n"
        "Your inputs are mounted at:\n"
        "  /workspace/curriculum/  — one .txt per course (read every file)\n"
        "  /workspace/data/job_postings.json  — role-tagged postings dataset\n\n"
        "Follow your system prompt: extract the actually-taught skillset from "
        "the curriculum (specific tools/libs/syntax — not generic 'databases'), "
        f"tally market demand from postings WHERE role == \"{role}\", and "
        "identify gaps according to the rules above (extend partial coverage; "
        "respect depth bound; cross-domain skills out of scope). Cite specific "
        "posting IDs (post_001 etc.) AND, for each gap, a specific slide "
        "reference proving partial coverage of the topic. Output the JSON "
        "exactly as specified. Do not narrate progress in chat."
    )


def _model_dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _extract_text_from_agent_message(event: Any) -> str:
    chunks: list[str] = []
    for block in getattr(event, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "".join(chunks)


from stale.agents._json_extract import extract_json_block as _extract_json_block


def run_market_fit(
    curriculum_dir: Path,
    role: str,
    *,
    specialization: str | None = None,
    scope: str | None = None,
    postings_path: Path = DEFAULT_POSTINGS,
    limit: int | None = None,
    courses_filter: list[str] | None = None,
    output_dir: Path | None = None,
    auditor_findings: list[dict] | None = None,
) -> dict[str, Any]:
    if role not in VALID_ROLES:
        raise SystemExit(f"Invalid role: {role!r}. Valid: {sorted(VALID_ROLES)}")
    if not postings_path.exists():
        raise SystemExit(
            f"Postings dataset not found: {postings_path}. "
            "Run `python data/build_postings.py` first."
        )

    load_dotenv(ENV_FILE)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    agent_id = os.environ.get("STALE_MARKET_FIT_AGENT_ID")
    env_id = os.environ.get("STALE_ENVIRONMENT_ID")
    if not api_key or not agent_id or not env_id:
        raise SystemExit(
            "Missing one of ANTHROPIC_API_KEY, STALE_MARKET_FIT_AGENT_ID, "
            "STALE_ENVIRONMENT_ID. Run `python -m stale.setup_agents` first."
        )

    client = anthropic.Anthropic(api_key=api_key)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = output_dir or (OUTPUT_ROOT / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    text_path = out_dir / "agent_text.md"
    json_path = out_dir / "market_fit.json"
    meta_path = out_dir / "session_meta.json"

    print(f"[market_fit] role={role} run_id={run_id}")
    print(f"[market_fit] output -> {out_dir}")

    print(f"[market_fit] extracting curriculum from {curriculum_dir} ...")
    courses = extract_curriculum(curriculum_dir)
    if not courses:
        raise SystemExit(f"No courses found under {curriculum_dir}")
    if courses_filter:
        available = {n.lower(): n for n in courses}
        picked: dict[str, str] = {}
        for q in courses_filter:
            ql = q.lower()
            if ql in available:
                picked[available[ql]] = courses[available[ql]]
                continue
            matches = [n for nl, n in available.items() if ql in nl]
            if len(matches) == 1:
                picked[matches[0]] = courses[matches[0]]
            elif not matches:
                raise SystemExit(f"No course matched '{q}'. "
                                 f"Available: {list(courses)}")
            else:
                raise SystemExit(f"Ambiguous '{q}' matches {matches}.")
        courses = picked
        print(f"[market_fit] course filter -> {list(courses)}")
    if limit is not None:
        courses = dict(list(courses.items())[:limit])
        print(f"[market_fit] limit={limit}: {list(courses)}")
    print(f"[market_fit] {len(courses)} course(s); "
          f"total chars = {sum(len(t) for t in courses.values()):,}")

    print("[market_fit] uploading curriculum ...")
    uploaded = upload_curriculum(client, courses)
    for name, info in uploaded.items():
        print(f"  - {name} -> {info['file_id']}")

    print(f"[market_fit] uploading postings dataset ({postings_path.name}) ...")
    postings_info = upload_data_file(client, str(postings_path))
    print(f"  - {postings_path.name} -> {postings_info['file_id']}  "
          f"({postings_info['mount_path']})")

    resources = curriculum_resources(uploaded) + [data_resource(postings_info)]

    print("[market_fit] creating session ...")
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        resources=resources,
        title=f"stale-market-fit-{role}-{run_id}",
    )
    print(f"[market_fit] session_id = {session.id}")

    meta_path.write_text(json.dumps({
        "run_id": run_id,
        "role": role,
        "session_id": session.id,
        "agent_id": agent_id,
        "environment_id": env_id,
        "courses": uploaded,
        "postings": postings_info,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))

    def _send_kickoff() -> None:
        print("[market_fit] sending kickoff ...")
        client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text",
                             "text": _kickoff_message(role, specialization,
                                                      scope, auditor_findings)}],
            }],
        )

    from stale.agents._sse import consume_session_stream
    print("[market_fit] opening event stream ...")
    full_text, _last_status = consume_session_stream(
        client, session.id,
        label="market_fit",
        events_path=events_path,
        text_path=text_path,
        on_stream_open=_send_kickoff,
    )
    parsed = _extract_json_block(full_text)
    if parsed is None:
        print("[market_fit] WARNING: could not parse JSON. "
              f"Raw text -> {text_path}", file=sys.stderr)
        return {"ok": False, "reason": "no_json", "session_id": session.id,
                "output_dir": str(out_dir)}

    json_path.write_text(json.dumps(parsed, indent=2))
    print(f"[market_fit] saved -> {json_path}")
    if parsed.get("summary"):
        print(f"[market_fit] summary: {json.dumps(parsed['summary'], indent=2)}")
    return {"ok": True, "session_id": session.id, "output_dir": str(out_dir),
            "result": parsed}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--role", required=True, choices=sorted(VALID_ROLES))
    p.add_argument("--curriculum", type=Path, default=DEFAULT_CURRICULUM)
    p.add_argument("--postings", type=Path, default=DEFAULT_POSTINGS)
    p.add_argument("--course", action="append", default=None,
                   help="Filter to specific course(s). Repeatable.")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    result = run_market_fit(
        args.curriculum, args.role,
        postings_path=args.postings,
        limit=args.limit,
        courses_filter=args.course,
        output_dir=args.output,
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
