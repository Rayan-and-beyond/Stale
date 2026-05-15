"""Topics session driver.

Pipeline:
  1. Load Market-fit's output JSON (the gap analysis).
  2. Upload the postings dataset for cross-referencing.
  3. Create a session against STALE_TOPICS_AGENT_ID.
  4. Send a kickoff containing the role, the gap JSON, and the course list.
  5. Consume events until session.status_idle, parse the final JSON.

Outputs (under output_dir/):
  - events.jsonl, agent_text.md, session_meta.json
  - topics.json    — parsed JSON output (ranked prescriptions)

Usage:
    python -m stale.agents.topics --role backend --market-fit <path/to/market_fit.json>
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

from stale.upload import data_resource, upload_data_file

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_POSTINGS = REPO_ROOT / "data" / "job_postings.json"
OUTPUT_ROOT = REPO_ROOT / "output" / "topics"

VALID_ROLES = {
    "backend", "frontend", "fullstack", "ml_data",
    "mobile", "devops_sre", "security", "embedded", "game",
}


def _kickoff_message(role: str, gap_json: dict, course_list: list[str],
                     specialization: str | None = None,
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
            "## Critical prescription rules\n\n"
            "**Rule 1 — Each prescription extends a partially-covered topic.** "
            "The Market-fit gap list below was already generated under this "
            "rule (gaps with slide citations proving partial coverage). Your "
            "prescriptions must extend those partially-covered topics, not "
            "introduce wholly new topics. The `fits_in_course` field must "
            "reference the specific course week/chapter where the partial "
            "coverage exists — not just any course in the list.\n\n"
            "**Rule 2 — Respect the depth bound above.** For an introductory "
            "survey, prescribe conceptual extensions; do not prescribe "
            "implementation walkthroughs, framework tutorials, or theoretical "
            "deep dives. For a hands-on lab, prescribe implementation "
            "extensions; do not prescribe abstract theory the course wasn't "
            "trying to teach. The phrasing of each `topic` and "
            "`headline_one_liner` must reflect the course's pedagogical level "
            "— not push it beyond.\n\n"
            "**Rule 3 — If a gap from Market-fit appears to violate either "
            "rule, omit it from your prescriptions.** Topics is the last line "
            "of defense against scope/depth drift. Better to recommend fewer "
            "items honestly than to pad the list.\n"
        )
    spec_block = ""
    if specialization:
        spec_block = (
            f"\nUser-stated specialization within {role}: "
            f"\"{specialization}\". When ranking prescriptions, prefer ones "
            "most relevant to this specialization — but stay strictly anchored "
            "to the gaps from the market-fit output below; do not invent new "
            "gaps. Rank order is still set by `market_frequency_pct`; "
            "specialization breaks ties and shapes the headline phrasing.\n"
        )
    auditor_block = ""
    if auditor_findings:
        # Compact view: only what Topics needs to avoid recommending. Drop
        # citation_excerpt (long) but keep claim, status, suggested_replacement.
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
            "The Auditor agent flagged the following patterns in the course "
            "as deprecated, insecure, or outdated. **Do not recommend "
            "extending or building new prescriptions on top of these "
            "patterns.** If a market-fit gap can only be filled by extending "
            "a flagged pattern, your prescription must explicitly *replace* "
            "the flagged pattern with the modern alternative named in "
            "`suggested_replacement`, not extend it.\n\n"
            "Concrete examples of correct behavior:\n"
            "- If Auditor flags `AsyncTask` as deprecated and Market-fit "
            "wants REST API consumption, do NOT prescribe \"build REST inside "
            "AsyncTask.\" Instead prescribe \"replace the AsyncTask section "
            "with HttpURLConnection on a java.util.concurrent.Executor + "
            "Handler(Looper.getMainLooper()), or Kotlin coroutines if the "
            "course will be migrated.\"\n"
            "- If Auditor flags `MD5` for password hashing and Market-fit "
            "wants modern auth, do NOT prescribe \"add salt to MD5.\" "
            "Prescribe replacing MD5 with Argon2id / bcrypt.\n\n"
            "If a flagged pattern appears in a `prerequisite_chain` or "
            "`fits_in_course_rationale` of any prescription you produce, "
            "you have made an error — rewrite it.\n\n"
            "```json\n"
            + json.dumps(compact, indent=2)
            + "\n```\n"
        )
    return (
        f"Target role: {role}{scope_block}{auditor_block}{spec_block}\n\n"
        f"Course list (the curriculum being audited):\n"
        + "\n".join(f"  - {c}" for c in course_list)
        + "\n\n"
        "Market-fit gap analysis (your input):\n"
        "```json\n"
        + json.dumps(gap_json, indent=2)
        + "\n```\n\n"
        "Job postings dataset is also mounted at "
        "`/workspace/data/job_postings.json` for cross-referencing posting IDs.\n\n"
        "Follow your system prompt: produce a ranked prescription list (top 10 max) "
        "anchored to the gaps above, ranked strictly by `market_frequency_pct`. "
        "For each prescription, name a real existing course where it should fit "
        "(or null if none). Cite specific posting IDs, do not invent gaps. "
        "Output the JSON exactly as specified."
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


def run_topics(
    role: str,
    gap_json: dict,
    course_list: list[str],
    *,
    specialization: str | None = None,
    scope: str | None = None,
    postings_path: Path = DEFAULT_POSTINGS,
    output_dir: Path | None = None,
    auditor_findings: list[dict] | None = None,
) -> dict[str, Any]:
    if role not in VALID_ROLES:
        raise SystemExit(f"Invalid role: {role!r}. Valid: {sorted(VALID_ROLES)}")

    load_dotenv(ENV_FILE)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    agent_id = os.environ.get("STALE_TOPICS_AGENT_ID")
    env_id = os.environ.get("STALE_ENVIRONMENT_ID")
    if not api_key or not agent_id or not env_id:
        raise SystemExit(
            "Missing one of ANTHROPIC_API_KEY, STALE_TOPICS_AGENT_ID, "
            "STALE_ENVIRONMENT_ID. Run `python -m stale.setup_agents` first."
        )

    client = anthropic.Anthropic(api_key=api_key)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = output_dir or (OUTPUT_ROOT / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    text_path = out_dir / "agent_text.md"
    json_path = out_dir / "topics.json"
    meta_path = out_dir / "session_meta.json"

    print(f"[topics] role={role} run_id={run_id}")
    print(f"[topics] output -> {out_dir}")

    print("[topics] uploading postings dataset ...")
    postings_info = upload_data_file(client, str(postings_path))
    resources = [data_resource(postings_info)]

    print("[topics] creating session ...")
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        resources=resources,
        title=f"stale-topics-{role}-{run_id}",
    )
    print(f"[topics] session_id = {session.id}")

    meta_path.write_text(json.dumps({
        "run_id": run_id,
        "role": role,
        "session_id": session.id,
        "agent_id": agent_id,
        "environment_id": env_id,
        "course_list": course_list,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))

    def _send_kickoff() -> None:
        print("[topics] sending kickoff ...")
        client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text",
                             "text": _kickoff_message(role, gap_json, course_list,
                                                      specialization, scope,
                                                      auditor_findings)}],
            }],
        )

    from stale.agents._sse import consume_session_stream
    print("[topics] opening event stream ...")
    full_text, _last_status = consume_session_stream(
        client, session.id,
        label="topics",
        events_path=events_path,
        text_path=text_path,
        on_stream_open=_send_kickoff,
    )
    parsed = _extract_json_block(full_text)
    if parsed is None:
        print("[topics] WARNING: could not parse JSON. "
              f"Raw text -> {text_path}", file=sys.stderr)
        return {"ok": False, "reason": "no_json", "session_id": session.id,
                "output_dir": str(out_dir)}

    json_path.write_text(json.dumps(parsed, indent=2))
    print(f"[topics] saved -> {json_path}")
    if parsed.get("summary"):
        print(f"[topics] summary: {json.dumps(parsed['summary'], indent=2)}")
    return {"ok": True, "session_id": session.id, "output_dir": str(out_dir),
            "result": parsed}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--role", required=True, choices=sorted(VALID_ROLES))
    p.add_argument("--market-fit", type=Path, required=True,
                   help="Path to market_fit.json from a previous market-fit run")
    p.add_argument("--postings", type=Path, default=DEFAULT_POSTINGS)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    if not args.market_fit.exists():
        raise SystemExit(f"Market-fit file not found: {args.market_fit}")
    gap_json = json.loads(args.market_fit.read_text())
    course_list = sorted({c for tag in gap_json.get("taught_skillset", [])
                          for c in tag.get("courses", [])})

    result = run_topics(
        args.role, gap_json, course_list,
        postings_path=args.postings,
        output_dir=args.output,
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
