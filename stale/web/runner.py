"""Two-stage background pipeline.

Stage 1 — fires when the user uploads slide decks. Runs two things in parallel:
    Auditor (role-agnostic)  →  verifier
    Scope detector  →  scope.json

    Scope detection is fast (~10s, ~$0.10) so its result lands well before
    the auditor finishes. The role-prompt UI uses the scope output to
    suggest 2–4 relevant roles as quick-pick chips while keeping the
    free-text input as the primary path.

Stage 2 — fires when the user submits a target role (free text → Haiku snap):
    Market-fit + Topics, both told the detected scope `one_liner` so they
    bound their gap analysis to what the course is *actually* teaching.
    Market-fit goes first; Topics consumes its output.

Output layout under run_dir/:
    meta.json
    curriculum/<course>/...
    scope.json                       (Stage 1, written by scope detector)
    auditor/{events.jsonl, findings.json, verified.json, ...}
    market_fit/{events.jsonl, market_fit.json, ...}
    topics/{events.jsonl, topics.json, ...}
    stage1.marker                    (auditor finished + verified)
    done.marker                      (whole pipeline done)
    error.log                        (pipeline-level error)
"""

from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from stale.agents.auditor import run_auditor
from stale.agents.market_fit import run_market_fit
from stale.agents.topics import run_topics
from stale.scope_detector import detect_scope
from stale.tools.extract import extract_curriculum
from stale.verify import verify_findings


def _safe_call(fn, label: str, error_target: Path, *args, **kwargs) -> Any:
    """Run `fn`, capturing any exception into `error_target/error.log`.
    Returns the function's value on success, None on failure.
    """
    try:
        return fn(*args, **kwargs)
    except SystemExit as e:
        error_target.mkdir(parents=True, exist_ok=True)
        (error_target / "error.log").write_text(f"[{label}] SystemExit: {e}\n")
    except Exception as e:
        error_target.mkdir(parents=True, exist_ok=True)
        (error_target / "error.log").write_text(
            f"[{label}] {type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        )
    return None


def _detect_scope_for_run(curriculum_root: Path, course: str, run_dir: Path) -> dict | None:
    """Extract the single uploaded course's text and run scope detection.
    Writes `scope.json` at run-dir root. Failures are logged to
    `scope_error.log` but don't crash the pipeline — the run page falls
    back to "all roles" chips when scope is missing."""
    try:
        courses = extract_curriculum(curriculum_root)
        if not courses:
            (run_dir / "scope_error.log").write_text(
                f"no courses extracted from {curriculum_root}\n"
            )
            return None
        # Find the course we uploaded (substring match, same convention as the agents)
        course_lower = course.lower()
        text: str | None = None
        for name, body in courses.items():
            if course_lower in name.lower() or name.lower() in course_lower:
                text = body
                break
        if text is None:
            text = next(iter(courses.values()))  # fallback to first course
        result = detect_scope(text)
        (run_dir / "scope.json").write_text(json.dumps(result, indent=2))
        return result
    except Exception as e:
        (run_dir / "scope_error.log").write_text(
            f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        )
        return None


def run_stage1(run_id: str, course: str, run_dir: Path) -> None:
    """Stage 1 — Auditor + scope detection in parallel; verify after auditor."""
    started = time.time()
    auditor_dir = run_dir / "auditor"
    auditor_dir.mkdir(parents=True, exist_ok=True)
    curriculum_root = run_dir / "curriculum"

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_auditor = ex.submit(
                _safe_call, run_auditor, "auditor", auditor_dir,
                curriculum_dir=curriculum_root,
                courses_filter=[course],
                output_dir=auditor_dir,
            )
            f_scope = ex.submit(
                _detect_scope_for_run, curriculum_root, course, run_dir
            )

            # Verify auditor citations as soon as the auditor finishes.
            f_auditor.result()
            findings_path = auditor_dir / "findings.json"
            if findings_path.exists():
                _safe_call(verify_findings, "verify", auditor_dir, findings_path)

            # Make sure scope finished before stage1 marker (it should be
            # done long before this point — auditor takes ~5–7 min, scope
            # ~10s — but block here in case of an outlier)
            f_scope.result()

        (run_dir / "stage1.marker").write_text(json.dumps({
            "elapsed_sec": round(time.time() - started, 1),
            "auditor_ok": (auditor_dir / "findings.json").exists(),
            "scope_ok": (run_dir / "scope.json").exists(),
        }, indent=2))
    except Exception as e:
        (run_dir / "error.log").write_text(
            f"stage1: {type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        )


def run_stage2(
    run_id: str,
    course: str,
    role: str,
    specialization: str | None,
    scope: dict | None,
    run_dir: Path,
) -> None:
    """Stage 2 — Market-fit (with role + specialization + scope), then Topics."""
    started = time.time()
    market_fit_dir = run_dir / "market_fit"
    topics_dir = run_dir / "topics"
    market_fit_dir.mkdir(parents=True, exist_ok=True)
    topics_dir.mkdir(parents=True, exist_ok=True)
    curriculum_root = run_dir / "curriculum"

    # Pass the full scope dict — agents now read subject_area,
    # pedagogical_level, course_intent, depth_bound, and one_liner.
    # Strip _usage telemetry; agents don't need it.
    scope_payload: dict | None = None
    if scope:
        scope_payload = {k: v for k, v in scope.items() if not k.startswith("_")}

    try:
        # Feed Auditor's verified findings into BOTH Market-fit and Topics so
        # neither tab recommends extending patterns flagged as deprecated/insecure.
        auditor_findings: list[dict] = []
        kept_path = run_dir / "auditor" / "findings_kept.json"
        if kept_path.exists():
            try:
                auditor_findings = json.loads(kept_path.read_text()).get("findings", [])
            except Exception:
                auditor_findings = []

        mf_result = _safe_call(
            run_market_fit, "market_fit", market_fit_dir,
            curriculum_dir=curriculum_root,
            role=role,
            specialization=specialization,
            scope=scope_payload,
            courses_filter=[course],
            output_dir=market_fit_dir,
            auditor_findings=auditor_findings,
        )

        if isinstance(mf_result, dict) and mf_result.get("ok"):
            gap_json = mf_result.get("result") or {}
            course_list = sorted({
                c
                for tag in gap_json.get("taught_skillset", [])
                for c in tag.get("courses", [])
            }) or [course]
            _safe_call(
                run_topics, "topics", topics_dir,
                role=role,
                specialization=specialization,
                scope=scope_payload,
                gap_json=gap_json,
                course_list=course_list,
                output_dir=topics_dir,
                auditor_findings=auditor_findings,
            )

        (run_dir / "done.marker").write_text(json.dumps({
            "elapsed_sec": round(time.time() - started, 1),
            "market_fit_ok": (market_fit_dir / "market_fit.json").exists(),
            "topics_ok": (topics_dir / "topics.json").exists(),
        }, indent=2))
    except Exception as e:
        (run_dir / "error.log").write_text(
            f"stage2: {type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        )
