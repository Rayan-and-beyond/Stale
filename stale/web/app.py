"""FastAPI app for Stale.

Two-stage flow:
  1. User uploads slide decks → Stage 1 (Auditor) fires immediately.
  2. While stage 1 is running (or after it finishes), the run page shows a
     free-text role input. User types their target role description.
  3. POST /run/{run_id}/role → Haiku resolver maps the free text to one of 9
     enums + a specialization hint → Stage 2 (Market-fit + Topics) fires.

Routes:
  GET  /                              — product intro
  GET  /start                         — upload form + recent runs
  POST /upload                        — kick off stage 1
  GET  /run/{run_id}                  — run page (Findings / Market-fit / Topics tabs)
  POST /run/{run_id}/role             — submit role, kick off stage 2
  GET  /run/{run_id}/state            — JSON status (stage, agents, role)
  GET  /run/{run_id}/findings.json    — verified Auditor findings
  GET  /run/{run_id}/market_fit.json  — Market-fit JSON
  GET  /run/{run_id}/topics.json      — Topics JSON
  GET  /run/{run_id}/events.stream    — SSE: tails all per-agent event logs

CLI-produced runs under output/auditor/<run_id>/ (legacy flat layout) are
also listed and rendered with their Findings.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEB_RUNS_ROOT = REPO_ROOT / "output" / "runs"
LEGACY_RUNS_ROOT = REPO_ROOT / "output" / "auditor"
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

ALLOWED_UPLOAD_EXT = {".pdf", ".pptx"}
SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.\- ]")

app = FastAPI(title="Stale")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["static_version"] = int((STATIC_DIR / "style.css").stat().st_mtime)
templates.env.globals["run_script_version"] = int((STATIC_DIR / "run.js").stat().st_mtime)

# User-facing labels for internal status enums. Anything not in the map
# falls through unchanged so new states fail loudly rather than silently.
STATUS_LABELS = {
    "awaiting_role": "Waiting for role",
    "pending":       "Pending",
    "running":       "Running",
    "done":          "Done",
    "error":         "Error",
    "n/a":           "—",
}
templates.env.filters["status_label"] = lambda s: STATUS_LABELS.get(s, s)

ISSUE_LABELS = {
    "dead": "No longer works",
    "deprecated": "Deprecated",
    "renamed": "Outdated",
    "outdated_teaching": "Outdated",
    "outdated": "Outdated",
    "doesnt_compile": "Will not run as shown",
    "runtime_wrong": "Gives wrong result",
    "conceptual": "Misleading idea",
    "wrong": "Incorrect",
    "insecure": "Security risk",
}


def _issue_label(finding: dict) -> str:
    status = str(finding.get("status") or "").lower()
    if status == "insecure":
        return ISSUE_LABELS[status]
    raw = str(finding.get("category") or status).lower()
    return ISSUE_LABELS.get(raw, raw.replace("_", " "))


templates.env.filters["issue_label"] = _issue_label

ISSUE_CLASSES = {
    "dead": "broken",
    "deprecated": "deprecated",
    "renamed": "outdated",
    "outdated_teaching": "outdated",
    "outdated": "outdated",
    "doesnt_compile": "run",
    "runtime_wrong": "run",
    "conceptual": "idea",
    "wrong": "incorrect",
    "insecure": "security",
}


def _issue_class(finding: dict) -> str:
    status = str(finding.get("status") or "").lower()
    if status == "insecure":
        return ISSUE_CLASSES[status]
    raw = str(finding.get("category") or status).lower()
    return ISSUE_CLASSES.get(raw, "neutral")


templates.env.filters["issue_class"] = _issue_class


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon():
    """Silence the browser's automatic favicon request — no asset, just 204."""
    from fastapi import Response
    return Response(status_code=204)


# ----------------------------------------------------------------------- helpers

def _safe_filename(name: str) -> str:
    return SAFE_NAME_RE.sub("_", name).strip() or "file"


def _run_dir(run_id: str) -> Path | None:
    for root in (WEB_RUNS_ROOT, LEGACY_RUNS_ROOT):
        d = root / run_id
        if d.exists() and d.is_dir():
            return d
    return None


def _is_legacy_run(run_dir: Path) -> bool:
    return (run_dir / "findings.json").exists() and not (run_dir / "auditor").exists()


def _agent_dir(run_dir: Path, agent: str) -> Path:
    if _is_legacy_run(run_dir) and agent == "auditor":
        return run_dir
    return run_dir / agent


def _read_meta(run_dir: Path) -> dict[str, Any]:
    p = run_dir / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _write_meta(run_dir: Path, meta: dict[str, Any]) -> None:
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))


def _normalize_course_key(name: str) -> str:
    """Normalize course names for matching shipped demo metadata to local
    curriculum folders. Handles harmless differences like trailing spaces,
    ampersand vs. "and", punctuation, and "PDF" suffixes."""
    normalized = name.lower().replace("&", "and")
    normalized = re.sub(r"\b(pdf|pptx|slides?)\b", " ", normalized)
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _course_tokens(name: str) -> set[str]:
    normalized = name.lower().replace("&", "and")
    tokens = re.findall(r"[a-z0-9]+", normalized)
    return {t for t in tokens if t not in {"and", "pdf", "pptx", "slides"}}


def _find_curriculum_course_dir(course_name: str) -> Path | None:
    repo_curriculum = REPO_ROOT / "curriculum"
    if not course_name or not repo_curriculum.is_dir():
        return None

    target_key = _normalize_course_key(course_name)
    target_tokens = _course_tokens(course_name)
    best: tuple[float, Path] | None = None

    for child in repo_curriculum.iterdir():
        if not child.is_dir() or not any(child.iterdir()):
            continue
        child_key = _normalize_course_key(child.name)
        if child_key == target_key:
            return child

        child_tokens = _course_tokens(child.name)
        if not target_tokens or not child_tokens:
            continue
        overlap = target_tokens & child_tokens
        score = len(overlap) / max(len(target_tokens), len(child_tokens))
        if target_key and child_key and (target_key in child_key or child_key in target_key):
            score += 0.25
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, child)

    return best[1] if best else None


def _agent_status(run_dir: Path, agent: str, output_filename: str) -> str:
    d = _agent_dir(run_dir, agent)
    if not d.exists():
        return "pending"
    if (d / output_filename).exists():
        return "done"
    if (d / "error.log").exists():
        return "error"
    if (d / "events.jsonl").exists():
        return "running"
    return "pending"


def _stage1_done(run_dir: Path) -> bool:
    return ((run_dir / "stage1.marker").exists()
            or (_agent_dir(run_dir, "auditor") / "findings_kept.json").exists()
            or (_agent_dir(run_dir, "auditor") / "findings.json").exists())


def _stage2_started(run_dir: Path) -> bool:
    return bool(_read_meta(run_dir).get("role"))


def _resolve_retest_curriculum(run_dir: Path) -> Path | None:
    """Return a directory we can pass as `curriculum_dir` to run_market_fit
    for re-testing this run with a new role. Tries two locations:
      1. run_dir/curriculum/ — the canonical per-run copy (present for
         locally-generated runs, missing for shipped curated demos).
      2. <repo>/curriculum/<course_dir_name>/ — the user's local
         curriculum tree, used for retesting shipped demos against the
         user's own files. We wrap the single course in a temp parent so
         extract_curriculum sees it as a one-course tree."""
    own = run_dir / "curriculum"
    if own.is_dir():
        for child in own.iterdir():
            if child.is_dir() and any(child.iterdir()):
                return own
    course_dir_name = (_read_meta(run_dir).get("course_dir_name")
                       or _safe_filename(_read_meta(run_dir).get("course", "")))
    if not course_dir_name:
        return None
    return (
        _find_curriculum_course_dir(course_dir_name)
        or _find_curriculum_course_dir(_read_meta(run_dir).get("course", ""))
    )


def _can_retest_with_new_role(run_dir: Path) -> bool:
    """A run can be re-tested with a new role iff the auditor finished AND
    a curriculum source is reachable (either run_dir/curriculum/ or the
    repo-level curriculum/<course>/ tree)."""
    if _is_legacy_run(run_dir):
        return False
    auditor = _agent_dir(run_dir, "auditor")
    if not (auditor / "findings_kept.json").exists() \
            and not (auditor / "findings.json").exists():
        return False
    return _resolve_retest_curriculum(run_dir) is not None


def _run_status(run_dir: Path) -> str:
    """Coarse pipeline status for status-pill rendering."""
    if (run_dir / "error.log").exists():
        return "error"
    marker = run_dir / "done.marker"
    if marker.exists():
        try:
            payload = json.loads(marker.read_text())
            if payload.get("market_fit_ok") is False or payload.get("topics_ok") is False:
                return "error"
        except (json.JSONDecodeError, OSError):
            pass
        return "done"
    if _is_legacy_run(run_dir):
        return "done" if (run_dir / "findings_kept.json").exists() else "running"
    if _stage1_done(run_dir) and not _stage2_started(run_dir):
        return "awaiting_role"
    if any((run_dir / sub / "events.jsonl").exists()
           for sub in ("auditor", "market_fit", "topics")):
        return "running"
    return "pending"


def _list_runs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for root in (WEB_RUNS_ROOT, LEGACY_RUNS_ROOT):
        if not root.exists():
            continue
        for d in sorted(root.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            entry: dict[str, Any] = {
                "run_id": d.name,
                "status": _run_status(d),
                "source": "web" if root == WEB_RUNS_ROOT else "cli",
            }
            entry.update(_read_meta(d))
            sess_meta = d / "session_meta.json"  # legacy only
            if sess_meta.exists():
                try:
                    sm = json.loads(sess_meta.read_text())
                    if "course" not in entry and sm.get("courses"):
                        entry["course"] = next(iter(sm["courses"].keys()))
                    entry["started_at"] = entry.get("started_at") or sm.get("started_at")
                except json.JSONDecodeError:
                    pass
            kept = _agent_dir(d, "auditor") / "findings_kept.json"
            raw = _agent_dir(d, "auditor") / "findings.json"
            if kept.exists():
                try:
                    payload = json.loads(kept.read_text())
                    entry["findings_count"] = len(payload.get("findings", []))
                except json.JSONDecodeError:
                    pass
            # Skip ghost runs: directories with no auditor output and no
            # in-progress signal. These are aborted shells from earlier dev
            # cycles and would render as broken on click.
            if (
                not kept.exists()
                and not raw.exists()
                and entry["status"] in ("pending", "done")
            ):
                continue
            out.append(entry)
    return out


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _summary_for_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    courses: list[str] = []
    for finding in findings:
        severity = str(finding.get("severity") or "low").lower()
        by_severity[severity] = by_severity.get(severity, 0) + 1
        course = finding.get("course")
        if course and course not in courses:
            courses.append(course)
    return {
        "total_findings": len(findings),
        "by_severity": by_severity,
        "courses_audited": courses,
    }


def _normalize_findings_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        return payload
    computed = _summary_for_findings(findings)
    existing = payload.get("summary", {})
    if isinstance(existing, dict) and "verification" in existing:
        computed["verification"] = existing["verification"]
    payload["summary"] = computed
    return payload


def _load_findings(run_dir: Path) -> dict[str, Any] | None:
    auditor = _agent_dir(run_dir, "auditor")
    return _normalize_findings_payload(
        _load_json(auditor / "findings_kept.json")
        or _load_json(auditor / "findings.json")
    )


def _load_verified(run_dir: Path) -> dict[str, Any] | None:
    return _load_json(_agent_dir(run_dir, "auditor") / "verified.json")


def _load_market_fit(run_dir: Path) -> dict[str, Any] | None:
    return _load_json(run_dir / "market_fit" / "market_fit.json")


def _load_topics(run_dir: Path) -> dict[str, Any] | None:
    return _load_json(run_dir / "topics" / "topics.json")


def _load_scope(run_dir: Path) -> dict[str, Any] | None:
    return _load_json(run_dir / "scope.json")


# Friendly labels rendered on quick-pick role chips. The text gets dropped
# into the free-text input on click; the Haiku resolver maps it back to
# the same enum on submit. We don't bypass the resolver — keeping one path
# for "free text in → role out" is simpler than maintaining two.
ROLE_CHIP_LABELS = {
    "backend": "backend engineer",
    "frontend": "frontend engineer",
    "fullstack": "fullstack developer",
    "ml_data": "ML / data engineer",
    "mobile": "mobile engineer",
    "devops_sre": "DevOps / SRE",
    "security": "security engineer",
    "embedded": "embedded / firmware engineer",
    "game": "game developer",
}


def _domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)/?", url)
    return m.group(1) if m else url


def _pct(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x or "")
    if v <= 1.0:
        v *= 100
    return f"{v:.0f}%"


def _course_focus_label(scope: dict[str, Any] | None) -> str:
    if not scope:
        return ""
    subject = str(scope.get("subject_area") or "").strip()
    if subject.lower().startswith("mobile application programming"):
        return "Android application development"
    return subject


# ----------------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/start", response_class=HTMLResponse)
async def start_page(request: Request):
    return templates.TemplateResponse(
        request,
        "start.html",
        {"runs": _list_runs()},
    )


@app.post("/upload")
async def upload(
    course: str = Form(...),
    files: list[UploadFile] = File(...),
):
    course = course.strip() or "Untitled Course"
    run_id = "web_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = WEB_RUNS_ROOT / run_id
    course_dir_name = _safe_filename(course)
    curriculum_dir = run_dir / "curriculum" / course_dir_name
    curriculum_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    skipped: list[str] = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXT:
            skipped.append(f.filename)
            continue
        dest = curriculum_dir / _safe_filename(f.filename)
        with dest.open("wb") as fh:
            fh.write(await f.read())
        saved.append(dest.name)

    _write_meta(run_dir, {
        "course": course,
        "course_dir_name": course_dir_name,
        "files_saved": saved,
        "files_skipped": skipped,
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    if not saved:
        (run_dir / "error.log").write_text(
            "No supported files uploaded (.pdf or .pptx required)."
        )
        return RedirectResponse(f"/run/{run_id}", status_code=303)

    from stale.web.runner import run_stage1
    threading.Thread(
        target=run_stage1,
        args=(run_id, course_dir_name, run_dir),
        daemon=True,
    ).start()

    return RedirectResponse(f"/run/{run_id}", status_code=303)


@app.post("/run/{run_id}/role")
async def submit_role(run_id: str, role_text: str = Form(...)):
    rd = _run_dir(run_id)
    if rd is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if _is_legacy_run(rd):
        return JSONResponse({"error": "legacy runs cannot run stage 2"},
                            status_code=400)
    if _stage2_started(rd):
        return RedirectResponse(f"/run/{run_id}", status_code=303)

    from stale.role_resolver import resolve_role
    resolved = resolve_role(role_text)

    meta = _read_meta(rd)
    meta.update({
        "role_text": role_text.strip(),
        "role": resolved["role"],
        "specialization": resolved.get("specialization"),
        "role_confidence": resolved.get("confidence"),
        "role_reasoning": resolved.get("reasoning"),
        "role_resolved_at": datetime.now(timezone.utc).isoformat(),
    })
    _write_meta(rd, meta)

    scope = _load_scope(rd)

    from stale.web.runner import run_stage2
    threading.Thread(
        target=run_stage2,
        args=(
            run_id,
            meta.get("course_dir_name") or _safe_filename(meta.get("course", "")),
            resolved["role"],
            resolved.get("specialization"),
            scope,
            rd,
        ),
        daemon=True,
    ).start()

    return RedirectResponse(f"/run/{run_id}", status_code=303)


@app.post("/run/{run_id}/retest")
async def retest_with_new_role(run_id: str, role_text: str = Form(...)):
    """Re-run stage 2 (market-fit + topics) on an existing run's audit data
    with a different target role. Spawns a NEW run dir whose `_source_run`
    field points back at the original. Skips the expensive auditor stage
    entirely — the new run inherits the source's findings, scope, and
    stage1.marker."""
    src = _run_dir(run_id)
    if src is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _can_retest_with_new_role(src):
        return JSONResponse(
            {"error": "this run cannot be re-tested (audit incomplete or "
                      "curriculum stripped)"},
            status_code=400,
        )

    src_meta = _read_meta(src)
    course_dir_name = (src_meta.get("course_dir_name")
                       or _safe_filename(src_meta.get("course", "")))
    if not course_dir_name:
        return JSONResponse(
            {"error": "source run is missing course_dir_name"},
            status_code=400,
        )

    curriculum_src = _resolve_retest_curriculum(src)
    if curriculum_src is None:
        return JSONResponse(
            {"error": "no curriculum reachable for this run"},
            status_code=400,
        )

    from stale.role_resolver import resolve_role
    resolved = resolve_role(role_text)

    # New run id, same convention as web uploads.
    new_run_id = datetime.now(timezone.utc).strftime("web_%Y%m%dT%H%M%SZ")
    new_dir = WEB_RUNS_ROOT / new_run_id
    new_dir.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(timezone.utc).isoformat()
    new_meta = {
        "course": src_meta.get("course"),
        "course_dir_name": course_dir_name,
        "files_saved": src_meta.get("files_saved", []),
        "files_skipped": src_meta.get("files_skipped", []),
        "started_at": now_iso,
        "role_text": role_text.strip(),
        "role": resolved["role"],
        "specialization": resolved.get("specialization"),
        "role_confidence": resolved.get("confidence"),
        "role_reasoning": resolved.get("reasoning"),
        "role_resolved_at": now_iso,
        "_source_run": src.name,
    }
    _write_meta(new_dir, new_meta)

    # Build new_dir/curriculum/ as a one-course tree symlinking the source.
    # extract_curriculum walks subdirs and treats each as a course, so we
    # always present a parent dir containing exactly one course subdir.
    # `curriculum_src` is either an existing tree (run_dir/curriculum/) or
    # a single course dir (repo curriculum/<course>/); normalize both.
    new_curriculum = new_dir / "curriculum"
    new_curriculum.mkdir(exist_ok=True)
    if (curriculum_src / course_dir_name).is_dir():
        # curriculum_src is a tree; link the one course we care about.
        (new_curriculum / course_dir_name).symlink_to(curriculum_src / course_dir_name)
    else:
        # curriculum_src IS the course dir.
        (new_curriculum / course_dir_name).symlink_to(curriculum_src)

    auditor_dst = new_dir / "auditor"
    auditor_dst.mkdir(exist_ok=True)
    for fname in ("findings.json", "findings_kept.json", "verified.json",
                  "candidates.json", "skipped.json", "session_meta.json"):
        f = src / "auditor" / fname
        if f.exists():
            (auditor_dst / fname).write_bytes(f.read_bytes())

    if (src / "scope.json").exists():
        (new_dir / "scope.json").write_bytes((src / "scope.json").read_bytes())
    if (src / "stage1.marker").exists():
        (new_dir / "stage1.marker").write_bytes((src / "stage1.marker").read_bytes())

    scope = _load_scope(new_dir)

    from stale.web.runner import run_stage2
    threading.Thread(
        target=run_stage2,
        args=(
            new_run_id,
            course_dir_name,
            resolved["role"],
            resolved.get("specialization"),
            scope,
            new_dir,
        ),
        daemon=True,
    ).start()

    return RedirectResponse(f"/run/{new_run_id}", status_code=303)


@app.get("/run/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, run_id: str):
    rd = _run_dir(run_id)
    if rd is None:
        return HTMLResponse("Run not found", status_code=404)

    meta = _read_meta(rd)
    auditor = _agent_dir(rd, "auditor")
    if (auditor / "session_meta.json").exists():
        try:
            sm = json.loads((auditor / "session_meta.json").read_text())
            meta.setdefault("course", next(iter(sm.get("courses", {}).keys()), ""))
            meta.setdefault("started_at", sm.get("started_at"))
            meta["session_id"] = sm.get("session_id")
        except json.JSONDecodeError:
            pass

    findings_payload = _load_findings(rd) or {"findings": []}
    verified_payload = _load_verified(rd)
    market_fit = _load_market_fit(rd) or {}
    topics = _load_topics(rd) or {}
    scope = _load_scope(rd)
    error = (rd / "error.log").read_text() if (rd / "error.log").exists() else None

    # Build chip data for the role form — only when scope detection has run
    # AND we're still awaiting a role pick.
    role_chips = []
    if scope and scope.get("relevant_roles"):
        for r in scope["relevant_roles"]:
            label = ROLE_CHIP_LABELS.get(r)
            if label:
                role_chips.append({"role": r, "label": label})

    if verified_payload:
        ver_by_claim = {f.get("claim", ""): f.get("verification", {})
                        for f in verified_payload.get("findings", [])}
        for f in findings_payload.get("findings", []):
            f.setdefault("verification",
                         ver_by_claim.get(f.get("claim", ""), {"status": "unknown"}))

    dropped: list[dict[str, Any]] = []
    if verified_payload:
        kept_claims = {f.get("claim") for f in findings_payload.get("findings", [])}
        for f in verified_payload.get("findings", []):
            if f.get("claim") not in kept_claims:
                dropped.append(f)

    SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    raw_findings = sorted(
        findings_payload.get("findings", []),
        key=lambda x: SEVERITY_ORDER.get(str(x.get("severity", "low")).lower(), 9),
    )

    is_legacy = _is_legacy_run(rd)
    status = _run_status(rd)
    agent_status = {
        "auditor": _agent_status(rd, "auditor",
                                  "findings_kept.json"
                                  if (auditor / "findings_kept.json").exists()
                                  else "findings.json"),
        "market_fit": "n/a" if is_legacy else _agent_status(
            rd, "market_fit", "market_fit.json"),
        "topics": "n/a" if is_legacy else _agent_status(
            rd, "topics", "topics.json"),
    }
    awaiting_role = (not is_legacy) and (not _stage2_started(rd))
    can_retest = _can_retest_with_new_role(rd) and _stage2_started(rd)
    source_run_id = meta.get("_source_run")

    return templates.TemplateResponse(
        request,
        "run.html",
        {
            "run_id": run_id,
            "meta": meta,
            "status": status,
            "agent_status": agent_status,
            "is_legacy": is_legacy,
            "awaiting_role": awaiting_role,
            "can_retest": can_retest,
            "source_run_id": source_run_id,
            "stage1_done": _stage1_done(rd),
            "findings": raw_findings,
            "summary": findings_payload.get("summary", {}),
            "dropped": dropped,
            "verification_summary": (verified_payload or {}).get("verification_summary", {}),
            "market_fit": market_fit,
            "topics": topics,
            "scope": scope,
            "role_chips": role_chips,
            "error": error,
            "domain_of": _domain_of,
            "pct": _pct,
            "course_focus_label": _course_focus_label,
        },
    )


@app.get("/run/{run_id}/state")
async def run_state(run_id: str):
    rd = _run_dir(run_id)
    if rd is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    auditor = _agent_dir(rd, "auditor")
    meta = _read_meta(rd)
    return {
        "run_id": run_id,
        "status": _run_status(rd),
        "stage1_done": _stage1_done(rd),
        "stage2_started": _stage2_started(rd),
        "scope_done": (rd / "scope.json").exists(),
        "role": meta.get("role"),
        "specialization": meta.get("specialization"),
        "agents": {
            "auditor": (auditor / "findings_kept.json").exists()
                       or (auditor / "findings.json").exists(),
            "market_fit": (rd / "market_fit" / "market_fit.json").exists(),
            "topics": (rd / "topics" / "topics.json").exists(),
        },
    }


@app.get("/run/{run_id}/findings.json")
async def run_findings_json(run_id: str):
    rd = _run_dir(run_id)
    if rd is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _load_findings(rd) or {"findings": []}


@app.get("/run/{run_id}/market_fit.json")
async def run_market_fit_json(run_id: str):
    rd = _run_dir(run_id)
    if rd is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _load_market_fit(rd) or {}


@app.get("/run/{run_id}/topics.json")
async def run_topics_json(run_id: str):
    rd = _run_dir(run_id)
    if rd is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _load_topics(rd) or {}


@app.get("/run/{run_id}/events.stream")
async def run_events(run_id: str):
    rd = _run_dir(run_id)
    if rd is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    if _is_legacy_run(rd):
        sources: list[tuple[str, Path]] = [("auditor", rd / "events.jsonl")]
    else:
        sources = [
            ("auditor", rd / "auditor" / "events.jsonl"),
            ("market_fit", rd / "market_fit" / "events.jsonl"),
            ("topics", rd / "topics" / "events.jsonl"),
        ]

    async def gen():
        sent_per_source = {name: 0 for name, _ in sources}
        deadline = time.time() + 60 * 30
        while time.time() < deadline:
            any_new = False
            for name, path in sources:
                if not path.exists():
                    continue
                with path.open() as fh:
                    lines = fh.readlines()
                while sent_per_source[name] < len(lines):
                    line = lines[sent_per_source[name]].rstrip()
                    if line:
                        try:
                            obj = json.loads(line)
                            obj["_source"] = name
                            payload = json.dumps(obj)
                        except json.JSONDecodeError:
                            payload = line
                        yield f"data: {payload}\n\n"
                        any_new = True
                    sent_per_source[name] += 1
            status = _run_status(rd)
            if status in ("done", "error"):
                yield f"event: done\ndata: {json.dumps({'status': status})}\n\n"
                return
            await asyncio.sleep(0.5 if any_new else 1.0)
        yield "event: done\ndata: {\"status\":\"timeout\"}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
