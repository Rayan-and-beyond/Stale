import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from stale.web.app import STATIC_DIR, app

REPO_ROOT = Path(__file__).resolve().parent.parent
MOBILE_RUN = REPO_ROOT / "output" / "runs" / "web_20260504T162848Z"


def test_curated_run_renders():
    client = TestClient(app)

    response = client.get("/run/refactor_oop1_20260505T215900Z")

    assert response.status_code == 200
    assert "Object Oriented Programming 1" in response.text
    assert "Role:" in response.text
    assert "backend" in response.text


def test_run_page_script_does_not_reload_browsing_session():
    script = (STATIC_DIR / "run.js").read_text()

    assert "location.reload" not in script


def test_findings_cards_use_public_issue_labels_without_card_severity_pills():
    client = TestClient(app)

    response = client.get("/run/refactor_oop2_20260506T135257Z")

    assert response.status_code == 200
    assert "citations matched" not in response.text
    assert "Security risk" in response.text
    assert "No longer works" in response.text
    assert "Deprecated" in response.text
    assert "Misleading idea" in response.text
    assert not re.search(
        r"<article class=\"finding card[^\"]*\">\\s*<header class=\"finding-head\">\\s*<span class=\"sev-pill",
        response.text,
    )


def test_run_tabs_have_plain_english_tooltips():
    client = TestClient(app)

    response = client.get("/run/refactor_oop2_20260506T135257Z")

    assert response.status_code == 200
    assert 'data-tooltip="Issues found in the course content"' in response.text
    assert 'data-tooltip="How well the course fits the target role"' in response.text
    assert 'data-tooltip="Suggested updates to make the course stronger"' in response.text
    assert 'data-tooltip="Items hidden because checks did not pass."' not in response.text
    assert 'class="tab" data-tab="activity"' not in response.text
    assert 'class="tab" data-tab="dropped"' not in response.text
    assert not re.search(r'class="tab[^"]*"[^>]*\stitle=', response.text)


def test_market_fit_and_topics_hide_internal_dataset_details():
    client = TestClient(app)

    response = client.get("/run/refactor_oop2_20260506T135257Z")

    assert response.status_code == 200
    assert "sample postings analyzed" not in response.text
    assert "synthetic role-tagged postings" not in response.text
    assert "data/README.md" not in response.text
    assert "Cited postings:" not in response.text
    assert "Market demand frequencies" not in response.text


def test_index_uses_plain_finding_counts():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Slides age." in response.text
    assert "shouldn't!" in response.text
    assert "Upload your slides" in response.text
    assert "Upload your slides and Stale reads through them" in response.text
    assert "Issue:" in response.text
    assert "Why it&#39;s wrong" not in response.text
    assert "what to learn instead" in response.text
    assert "Hello" in response.text
    assert "real" in response.text
    assert "World!" in response.text
    assert "Drag and drop your PDF or PPTX lecture decks" not in response.text
    assert "slide-by-slide annotations" not in response.text
    assert "<h1>Stale</h1>" in response.text
    assert 'href="/start"' in response.text
    assert 'id="start-audit"' not in response.text
    assert "/static/home.js" not in response.text
    assert "motion-field" not in response.text
    assert "Audit preview" not in response.text
    assert "Upload slide decks" not in response.text


def test_start_page_contains_upload_and_recent_runs():
    client = TestClient(app)

    response = client.get("/start")

    assert response.status_code == 200
    assert "Upload slide decks" in response.text
    assert 'id="start-audit"' in response.text
    assert "1 verified findings" not in response.text
    assert "verified findings" not in response.text
    assert "1 Finding" in response.text


def test_run_page_no_longer_points_to_removed_activity_tab():
    client = TestClient(app)

    response = client.get("/run/refactor_oop2_20260506T135257Z")

    assert response.status_code == 200
    assert 'id="tab-activity"' not in response.text
    assert "Switch to <strong>Activity</strong>" not in response.text


def test_retest_button_appears_when_audit_done_and_curriculum_reachable():
    """The 'Run another role' retest form should render on a completed
    run when the curriculum is reachable (own dir or repo-level fallback)."""
    from stale.web.app import _can_retest_with_new_role
    client = TestClient(app)

    run_dir = REPO_ROOT / "output" / "runs" / "refactor_oop1_20260505T215900Z"
    repo_curriculum_oop1 = REPO_ROOT / "curriculum" / "Object Oriented Programming 1 "
    if not _can_retest_with_new_role(run_dir):
        import pytest
        pytest.skip(
            "OOP1 curriculum not on local disk — retest fallback not "
            "exercisable in this environment"
        )

    response = client.get("/run/refactor_oop1_20260505T215900Z")
    assert response.status_code == 200
    assert "Run another role" in response.text
    assert 'action="/run/refactor_oop1_20260505T215900Z/retest"' in response.text


def test_retest_button_appears_for_mobile_with_fuzzy_curriculum_match():
    """The shipped Mobile demo says "Mobile Application Development" while
    the local source folder is "Mobile Application PDF"; retest should still
    be available because those are the same course for this local app."""
    from stale.web.app import _can_retest_with_new_role
    client = TestClient(app)

    run_dir = REPO_ROOT / "output" / "runs" / "web_20260504T162848Z"
    assert _can_retest_with_new_role(run_dir)

    response = client.get("/run/web_20260504T162848Z")
    assert response.status_code == 200
    assert "Run another role" in response.text
    assert 'action="/run/web_20260504T162848Z/retest"' in response.text


def test_retest_button_appears_for_all_completed_demo_runs():
    """Every completed curated web run with a resolved role should be able to
    reuse its audit for another target role."""
    from stale.web.app import _can_retest_with_new_role
    client = TestClient(app)

    for run_dir in sorted((REPO_ROOT / "output" / "runs").iterdir()):
        if not run_dir.is_dir() or not (run_dir / "meta.json").exists():
            continue
        meta = json.loads((run_dir / "meta.json").read_text())
        if not meta.get("role") or not (run_dir / "auditor").is_dir():
            continue
        assert _can_retest_with_new_role(run_dir), run_dir.name
        response = client.get(f"/run/{run_dir.name}")
        assert response.status_code == 200
        assert "Run another role" in response.text


def test_retest_endpoint_rejects_unknown_run():
    client = TestClient(app)
    r = client.post("/run/does_not_exist/retest", data={"role_text": "backend"})
    assert r.status_code == 404


def test_derived_run_shows_source_link():
    """A run whose meta has _source_run should render a 'derived from' link."""
    client = TestClient(app)

    # The networks_security run from the recent stage-2 retest experiment
    # has _source_run set to web_20260504T212437Z.
    derived = sorted((REPO_ROOT / "output" / "runs").glob("networks_security_*"))
    if not derived:
        import pytest
        pytest.skip("no derived runs available to exercise the source-link path")
    response = client.get(f"/run/{derived[-1].name}")
    assert response.status_code == 200
    assert "Derived from" in response.text


def test_mobile_market_fit_does_not_extend_asynctask():
    """Cross-tab consistency: the Auditor flags AsyncTask as deprecated, so
    Market-fit must never recommend extending it. It can mention AsyncTask
    in the *taught skillset* (descriptive) and in *replace not extend*
    framing, but no gap rationale should describe extending or building on
    top of AsyncTask scaffolding."""
    market_fit = json.loads((MOBILE_RUN / "market_fit" / "market_fit.json").read_text())

    bad_phrases = [
        "inside the existing AsyncTask",
        "inside the AsyncTask",
        "extending AsyncTask",
        "extend AsyncTask",
        "build on AsyncTask",
        "on top of AsyncTask",
        "within the AsyncTask",
    ]
    for gap in market_fit.get("gaps", []):
        rationale = gap.get("rationale", "") or ""
        for phrase in bad_phrases:
            assert phrase.lower() not in rationale.lower(), (
                f"Mobile Market-fit gap {gap.get('skill')!r} recommends "
                f"extending a flagged-deprecated pattern: found {phrase!r} "
                f"in rationale. The Auditor flags AsyncTask as deprecated; "
                f"Market-fit must frame the gap as 'replace, not extend'."
            )


def test_kept_findings_summary_matches_visible_findings():
    client = TestClient(app)

    payload = client.get("/run/web_20260428T132556Z/findings.json").json()
    summary = payload["summary"]

    assert len(payload["findings"]) == 1
    assert summary["total_findings"] == 1
    assert summary["by_severity"]["medium"] == 1
    assert summary["by_severity"]["low"] == 0

    response = client.get("/run/web_20260428T132556Z")
    assert response.status_code == 200
    assert "1 medium" in response.text
    assert "1 low" not in response.text


def test_ai_run_market_fit_topics_are_internally_consistent():
    run = REPO_ROOT / "output" / "runs" / "web_20260428T132556Z"
    market_fit = json.loads((run / "market_fit" / "market_fit.json").read_text())
    topics = json.loads((run / "topics" / "topics.json").read_text())

    serialized = json.dumps({"market_fit": market_fit, "topics": topics}).lower()
    assert "prompt's worked example" not in serialized
    assert "11/12" not in serialized
    assert "11 of 12" not in serialized

    total_postings = market_fit["summary"]["total_postings_analyzed"]
    covered = set()
    for prescription in topics["prescriptions"]:
        covered.update(prescription["posting_refs"])
    assert len(covered) == total_postings
    assert "all 12 ml_data sample postings" in topics["summary"]["top_3_headline"]
