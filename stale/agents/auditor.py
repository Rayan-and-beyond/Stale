"""Auditor — two-stage pipeline (extractor → adjudicator).

Phase 1 — Extractor:
  Reads the curriculum and surfaces every candidate stale pattern with
  surrounding context. High recall; does not decide flag/skip.

Phase 2 — Adjudicator:
  Receives the candidates + the same curriculum. For each candidate, decides
  FLAG (final finding with primary-source citation) or SKIP (counterexample,
  paired correction nearby, no primary source, etc.). Burden of proof is on
  SKIP — default is FLAG.

Both phases are Managed Agent sessions on Anthropic's beta API. The Extractor
reuses STALE_AUDITOR_AGENT_ID (its system prompt is now the extractor prompt).
The Adjudicator uses STALE_ADJUDICATOR_AGENT_ID.

Outputs (under output_dir):
  - extractor/{events.jsonl, agent_text.md, session_meta.json}
  - candidates.json                    — extractor output
  - adjudicator/{events.jsonl, agent_text.md, session_meta.json}
  - findings.json                      — adjudicator output (same schema as
                                          before; consumed by verifier + UI)
  - skipped.json                       — adjudicator's skip log (auditable)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from stale.agents._json_extract import extract_json_block
from stale.agents._slide_ref_verifier import expand_short_claims, verify_slide_refs
from stale.tools.extract import extract_curriculum
from stale.upload import (
    curriculum_resources,
    upload_curriculum,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_CURRICULUM = REPO_ROOT / "curriculum"
OUTPUT_ROOT = REPO_ROOT / "output" / "auditor"

EXTRACTOR_KICKOFF = (
    "Surface every candidate stale pattern from the curriculum mounted at "
    "/workspace/curriculum/. Read every .txt file (one per course). Use grep "
    "to find related occurrences across slides. Recall over precision — do "
    "not decide flag vs skip; that is the Adjudicator's job. Emit the JSON "
    "output exactly as specified in your system prompt."
)

ADJUDICATOR_KICKOFF_TEMPLATE = (
    "Adjudicate the candidates below against the course materials mounted at "
    "/workspace/curriculum/. For each candidate, decide FLAG (real finding "
    "with primary-source citation) or SKIP (counterexample, paired correction "
    "nearby, no primary source). Default is FLAG; burden of proof is on SKIP. "
    "Use grep/read to verify context, web_fetch/web_search to back FLAGs with "
    "primary sources. Emit the JSON output exactly as specified in your "
    "system prompt.\n\n"
    "Candidates:\n```json\n{candidates_json}\n```"
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


def _stream_session(
    client: anthropic.Anthropic,
    *,
    agent_id: str,
    env_id: str,
    resources: list[dict],
    kickoff_text: str,
    label: str,
    out_dir: Path,
) -> tuple[str, dict[str, Any] | None]:
    """Run one agent session end-to-end. Streams events to disk, parses the
    final JSON code block, returns (session_id, parsed_dict|None)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    text_path = out_dir / "agent_text.md"
    meta_path = out_dir / "session_meta.json"

    print(f"[{label}] creating session ...")
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        resources=resources,
        title=f"stale-{label}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )
    print(f"[{label}] session_id = {session.id}")

    meta_path.write_text(json.dumps({
        "session_id": session.id,
        "agent_id": agent_id,
        "environment_id": env_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
    }, indent=2))

    print(f"[{label}] opening event stream ...")
    stream = client.beta.sessions.events.stream(session.id)

    print(f"[{label}] sending kickoff user message ...")
    client.beta.sessions.events.send(
        session.id,
        events=[{
            "type": "user.message",
            "content": [{"type": "text", "text": kickoff_text}],
        }],
    )

    agent_text_parts: list[str] = []
    started = time.time()
    last_status: str | None = None
    seen_event_keys: set[str] = set()
    max_reconnects = 6
    reconnects = 0

    def _event_key(ev_dict: dict) -> str:
        # Best-effort dedup: prefer event id, fall back to a stable hash of
        # type + first 200 chars of dump.
        eid = ev_dict.get("id") or ev_dict.get("event_id")
        if eid:
            return str(eid)
        return f"{ev_dict.get('type','?')}|{json.dumps(ev_dict, sort_keys=True)[:200]}"

    with events_path.open("w") as ev_log, text_path.open("w") as txt_log:
        done = False
        while not done:
            try:
                for event in stream:
                    ev_dict = _model_dump(event)
                    key = _event_key(ev_dict if isinstance(ev_dict, dict) else {})
                    if key in seen_event_keys:
                        continue  # drop duplicate from reconnect replay
                    seen_event_keys.add(key)

                    ev_type = getattr(event, "type", "?")
                    ev_log.write(json.dumps(ev_dict) + "\n")
                    ev_log.flush()

                    if ev_type == "agent.message":
                        chunk = _extract_text_from_agent_message(event)
                        if chunk:
                            agent_text_parts.append(chunk)
                            txt_log.write(chunk + "\n\n---\n\n")
                            txt_log.flush()
                            print(f"[{label}] agent.message ({len(chunk)} chars)")
                    elif ev_type == "agent.thinking":
                        print(f"[{label}] agent.thinking ...")
                    elif ev_type == "agent.tool_use":
                        tool_name = getattr(event, "name", "?")
                        print(f"[{label}] tool_use: {tool_name}")
                    elif ev_type == "agent.tool_result":
                        print(f"[{label}] tool_result")
                    elif ev_type.startswith("session.status_"):
                        last_status = ev_type
                        print(f"[{label}] {ev_type}")
                        if ev_type in ("session.status_idle",
                                       "session.status_terminated"):
                            done = True
                            break
                    elif ev_type == "session.error":
                        ev_dump = _model_dump(event)
                        retry = (ev_dump.get("error", {}) or {}).get("retry_status") if isinstance(ev_dump, dict) else None
                        if retry and retry.get("type") == "retrying":
                            print(f"[{label}] transient session.error "
                                  f"(server retrying): {ev_dump.get('error', {}).get('message', '?')}",
                                  file=sys.stderr)
                            # don't break — server is retrying internally
                        else:
                            print(f"[{label}] ERROR: {ev_dump}",
                                  file=sys.stderr)
                            done = True
                            break
                else:
                    # Iterator exhausted without an idle/terminated event —
                    # API closed the stream cleanly. Treat as done.
                    done = True
            except Exception as exc:
                if reconnects >= max_reconnects:
                    print(f"[{label}] stream error after {reconnects} "
                          f"reconnects, giving up: {type(exc).__name__}: {exc}",
                          file=sys.stderr)
                    raise
                reconnects += 1
                backoff = min(2 ** reconnects, 30)
                print(f"[{label}] stream error ({type(exc).__name__}); "
                      f"reconnecting in {backoff}s "
                      f"(reconnect {reconnects}/{max_reconnects})",
                      file=sys.stderr)
                time.sleep(backoff)
                stream = client.beta.sessions.events.stream(session.id)

    elapsed = time.time() - started
    print(f"[{label}] stream closed after {elapsed:.1f}s "
          f"(last status: {last_status}, reconnects: {reconnects})")

    full_text = "".join(agent_text_parts)
    parsed = extract_json_block(full_text)
    if parsed is None:
        print(f"[{label}] WARNING: could not parse JSON. Raw text in {text_path}",
              file=sys.stderr)
    return session.id, parsed


def run_auditor(
    curriculum_dir: Path,
    *,
    limit: int | None = None,
    courses_filter: list[str] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    load_dotenv(ENV_FILE)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    extractor_id = os.environ.get("STALE_AUDITOR_AGENT_ID")
    adjudicator_id = os.environ.get("STALE_ADJUDICATOR_AGENT_ID")
    env_id = os.environ.get("STALE_ENVIRONMENT_ID")
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY": api_key,
        "STALE_AUDITOR_AGENT_ID": extractor_id,
        "STALE_ADJUDICATOR_AGENT_ID": adjudicator_id,
        "STALE_ENVIRONMENT_ID": env_id,
    }.items() if not v]
    if missing:
        raise SystemExit(
            f"Missing env vars: {missing}. Did you run `python -m stale.setup_agents`?"
        )

    client = anthropic.Anthropic(api_key=api_key)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = output_dir or (OUTPUT_ROOT / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = out_dir / "candidates.json"
    findings_path = out_dir / "findings.json"
    skipped_path = out_dir / "skipped.json"

    print(f"[auditor] run_id = {run_id}")
    print(f"[auditor] output -> {out_dir}")

    print(f"[auditor] extracting curriculum from {curriculum_dir} ...")
    courses = extract_curriculum(curriculum_dir)
    if not courses:
        raise SystemExit(f"No courses found under {curriculum_dir}")
    if courses_filter:
        available = {name.lower(): name for name in courses}
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
        print(f"[auditor] course filter -> {list(courses)}")
    if limit is not None:
        courses = dict(list(courses.items())[:limit])
        print(f"[auditor] limit={limit}: keeping {list(courses)}")
    print(f"[auditor] {len(courses)} course(s); total chars = "
          f"{sum(len(t) for t in courses.values()):,}")

    print("[auditor] uploading curriculum to Files API ...")
    uploaded = upload_curriculum(client, courses)
    for name, info in uploaded.items():
        print(f"  - {name} -> {info['file_id']}  ({info['mount_path']})")
    resources = curriculum_resources(uploaded)

    # ──────────── Phase 1 — Extractor (resume if cached) ────────────
    if candidates_path.exists():
        candidates = json.loads(candidates_path.read_text())
        cand_list = candidates.get("candidates", [])
        print(f"\n[auditor] === Phase 1: Extractor — RESUMING from "
              f"{candidates_path.name} ({len(cand_list)} candidates) ===")
    else:
        print("\n[auditor] === Phase 1: Extractor ===")
        extractor_dir = out_dir / "extractor"
        _, candidates = _stream_session(
            client,
            agent_id=extractor_id,
            env_id=env_id,
            resources=resources,
            kickoff_text=EXTRACTOR_KICKOFF,
            label="extractor",
            out_dir=extractor_dir,
        )
        if candidates is None:
            return {"ok": False, "reason": "extractor_no_json",
                    "output_dir": str(out_dir)}
        candidates_path.write_text(json.dumps(candidates, indent=2))
        cand_list = candidates.get("candidates", [])
        print(f"[auditor] extractor surfaced {len(cand_list)} candidates "
              f"-> {candidates_path}")

    if not cand_list:
        # Empty findings file so the verifier and UI still work.
        findings_path.write_text(json.dumps({"findings": [],
            "summary": {"total_findings": 0, "by_severity": {},
                        "courses_audited": list(courses)}}, indent=2))
        skipped_path.write_text(json.dumps({"skipped": []}, indent=2))
        print("[auditor] no candidates — skipping adjudication")
        return {"ok": True, "output_dir": str(out_dir),
                "candidates": candidates,
                "findings": json.loads(findings_path.read_text())}

    # ──────────── Phase 2 — Adjudicator ────────────
    print("\n[auditor] === Phase 2: Adjudicator ===")
    adjudicator_dir = out_dir / "adjudicator"
    kickoff = ADJUDICATOR_KICKOFF_TEMPLATE.format(
        candidates_json=json.dumps(candidates, indent=2)
    )
    _, verdict = _stream_session(
        client,
        agent_id=adjudicator_id,
        env_id=env_id,
        resources=resources,
        kickoff_text=kickoff,
        label="adjudicator",
        out_dir=adjudicator_dir,
    )
    if verdict is None:
        return {"ok": False, "reason": "adjudicator_no_json",
                "output_dir": str(out_dir),
                "candidates": candidates}

    # Split into findings.json (downstream consumer expects this exact name +
    # schema) and skipped.json (audit log).
    raw_findings = verdict.get("findings", [])
    claim_summary = expand_short_claims(raw_findings, candidates)
    print(f"[auditor] claim expander: expanded={claim_summary['expanded']}")
    sref_summary = verify_slide_refs(raw_findings, courses)
    print(f"[auditor] slide_ref verifier: matched={sref_summary['matched']} "
          f"rewritten={sref_summary['rewritten']} "
          f"not_found={sref_summary['not_found']}")
    findings_obj = {
        "findings": raw_findings,
        "summary": {**verdict.get("summary", {}),
                    "claim_expansion": claim_summary,
                    "slide_ref_verification": sref_summary},
    }
    findings_path.write_text(json.dumps(findings_obj, indent=2))
    skipped_path.write_text(json.dumps({
        "skipped": verdict.get("skipped", []),
        "summary": verdict.get("summary", {}),
    }, indent=2))

    summary = verdict.get("summary", {})
    print(f"[auditor] adjudicator: flagged={summary.get('flagged', '?')} "
          f"skipped={summary.get('skipped', '?')}")
    print(f"[auditor] findings -> {findings_path}")
    print(f"[auditor] skipped log -> {skipped_path}")

    return {"ok": True, "output_dir": str(out_dir),
            "candidates": candidates, "findings": findings_obj,
            "skipped": verdict.get("skipped", [])}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--curriculum", type=Path, default=DEFAULT_CURRICULUM)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--course", action="append", default=None)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    result = run_auditor(args.curriculum, limit=args.limit,
                         courses_filter=args.course, output_dir=args.output)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
