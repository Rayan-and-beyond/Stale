"""Deterministic citation verifier.

For every finding the Auditor produced, we re-fetch the `citation_url`
ourselves and check that the `citation_excerpt` actually appears on that
page. Findings that fail are dropped from the kept set — they were either
fabricated, the URL is dead, or the excerpt does not match.

This catches the most common LLM failure mode (manufactured URLs / wrong
quotes) without needing a second agent. It does NOT verify whether the
`suggested_replacement` is itself current best practice — that's a harder
question that would need an LLM with web search.

Usage:
    python -m stale.verify <findings.json>

Outputs (next to the input file):
    verified.json        — every finding annotated with a `verification` block
    findings_kept.json   — only findings that passed verification
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; StaleAuditor/0.1)"
TIMEOUT_SEC = 20.0
MAX_RETRIES = 1


def _normalize(s: str) -> str:
    """Lowercase, decode HTML entities, collapse whitespace. Used on both the
    page text and the excerpt before substring comparison so trivial
    formatting differences don't cause false negatives."""
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.lower().strip()


def _candidate_phrases(excerpt: str) -> list[str]:
    """Break an excerpt into substrings that should each appear verbatim in
    the source. Strips parenthetical/bracketed attributions (citations like
    '(NIST SP 800-131A, §3)' rarely appear in the original), pulls out any
    quoted material as its own candidate (the auditor uses citation formats
    like `Source §X: "actual quote"` — the actual quote is what we expect to
    find in the source, not the attribution prefix), then splits on ellipses,
    semicolons, and sentence boundaries. Returns fragments sorted by length
    descending."""
    cleaned = re.sub(r"\([^)]*\)", " ", excerpt)
    cleaned = re.sub(r"\[[^\]]*\]", " ", cleaned)
    # Pull out anything wrapped in straight or smart double quotes — that's
    # the actual material we expect to find verbatim in the source.
    quoted = re.findall(r'"([^"]+)"|“([^”]+)”', cleaned)
    quoted_strs = [a or b for a, b in quoted]
    # Also split on ellipses, semicolons, and sentence boundaries.
    parts = re.split(r"\.\.\.|…|;|\.\s+(?=[A-Z\"'“])", cleaned)
    seen: set[str] = set()
    out: list[str] = []
    for p in (*quoted_strs, *parts):
        t = re.sub(r"\s+", " ", p).strip(" .,:—–-\"'“”")
        if len(t.split()) < 4:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    out.sort(key=len, reverse=True)
    return out


def _excerpt_in_page(excerpt: str, page_text: str) -> bool:
    """Verify the excerpt against the page. Strategy:
       1. Try the full normalized excerpt as a substring (cheap path).
       2. Otherwise extract candidate phrases (with parentheticals stripped)
          and check them against a copy of the page text with parentheticals
          ALSO stripped — this handles cases where the original NIST text has
          inline parenthetical examples that the auditor elided in the quote.
       3. Require at least one phrase of >=6 words verbatim, OR >=2 phrases.
    """
    haystack_full = _normalize(page_text)
    if _normalize(excerpt) in haystack_full:
        return True
    # Symmetric paren/bracket strip — the excerpt has them stripped, so the
    # page side must too for adjacent-words matching to work.
    page_no_parens = re.sub(r"\([^)]*\)", " ", page_text)
    page_no_parens = re.sub(r"\[[^\]]*\]", " ", page_no_parens)
    haystack = _normalize(page_no_parens)
    phrases = _candidate_phrases(excerpt)
    if not phrases:
        return False
    hits = 0
    for ph in phrases:
        norm = _normalize(ph)
        if norm in haystack or norm in haystack_full:
            hits += 1
            if len(ph.split()) >= 6:
                return True
    return hits >= 2


def _strip_html(html_text: str) -> str:
    """Crude HTML→text. Drops <script>/<style> blocks, then strips remaining
    tags. Good enough for substring matching against documentation pages."""
    html_text = re.sub(r"<script[^>]*>.*?</script>", " ", html_text,
                       flags=re.DOTALL | re.IGNORECASE)
    html_text = re.sub(r"<style[^>]*>.*?</style>", " ", html_text,
                       flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html_text)
    return text


def _extract_pdf(content: bytes) -> str:
    import fitz  # pymupdf

    doc = fitz.open(stream=content, filetype="pdf")
    out: list[str] = []
    for page in doc:
        out.append(page.get_text())
    doc.close()
    return "\n".join(out)


def _fetch(url: str, client: httpx.Client) -> tuple[bytes, str] | None:
    """Return (body_bytes, content_type) or None on failure."""
    last_err = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.get(url, follow_redirects=True, timeout=TIMEOUT_SEC)
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}"
                continue
            ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            return resp.content, ct
        except httpx.HTTPError as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(0.5)
    print(f"  [fetch failed] {url} — {last_err}", file=sys.stderr)
    return None


def _verify_one(finding: dict[str, Any], client: httpx.Client) -> dict[str, Any]:
    url = finding.get("citation_url", "")
    excerpt = finding.get("citation_excerpt", "")
    if not url:
        return {"status": "no_url"}
    if not excerpt:
        return {"status": "no_excerpt", "url": url}

    fetched = _fetch(url, client)
    if fetched is None:
        return {"status": "url_failed", "url": url}

    body, ct = fetched
    if "pdf" in ct or url.lower().endswith(".pdf"):
        try:
            page_text = _extract_pdf(body)
        except Exception as e:
            return {"status": "pdf_parse_failed", "url": url, "error": str(e)}
    elif "html" in ct or "xml" in ct or ct == "":
        page_text = _strip_html(body.decode("utf-8", errors="replace"))
    elif "text" in ct or "json" in ct:
        page_text = body.decode("utf-8", errors="replace")
    else:
        return {"status": "content_type_skip", "url": url, "content_type": ct}

    if _excerpt_in_page(excerpt, page_text):
        return {"status": "verified", "url": url, "content_type": ct}
    return {
        "status": "excerpt_not_found",
        "url": url,
        "content_type": ct,
        "excerpt_preview": excerpt[:120],
    }


def verify_findings(findings_path: Path) -> dict[str, Any]:
    data = json.loads(findings_path.read_text())
    findings = data.get("findings", [])
    print(f"[verify] {len(findings)} findings to check")

    annotated: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        for i, f in enumerate(findings, 1):
            v = _verify_one(f, client)
            counts[v["status"]] = counts.get(v["status"], 0) + 1
            annotated.append({**f, "verification": v})
            mark = "OK" if v["status"] == "verified" else "X "
            print(f"  [{i:>2}/{len(findings)}] {mark} {v['status']}  "
                  f"{f.get('claim', '')[:80]}")

    kept = [f for f in annotated if f["verification"]["status"] == "verified"]

    out_dir = findings_path.parent
    verified_path = out_dir / "verified.json"
    kept_path = out_dir / "findings_kept.json"
    verified_path.write_text(json.dumps(
        {**data, "findings": annotated,
         "verification_summary": {"by_status": counts,
                                  "kept": len(kept),
                                  "dropped": len(findings) - len(kept)}},
        indent=2,
    ))
    kept_severity: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in kept:
        sev = f.get("severity")
        if sev in kept_severity:
            kept_severity[sev] += 1
    kept_path.write_text(json.dumps(
        {**data, "findings": kept,
         "summary": {**data.get("summary", {}),
                     "total_findings": len(kept),
                     "by_severity": kept_severity,
                     "verification": {"kept": len(kept),
                                      "dropped_total": len(findings) - len(kept),
                                      "dropped_by_reason": {k: v for k, v in counts.items()
                                                            if k != "verified"}}}},
        indent=2,
    ))
    print(f"[verify] kept {len(kept)} / dropped {len(findings) - len(kept)}")
    print(f"[verify] by_status: {counts}")
    print(f"[verify] -> {verified_path}")
    print(f"[verify] -> {kept_path}")
    return {"kept": len(kept), "by_status": counts,
            "verified_path": str(verified_path),
            "kept_path": str(kept_path)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("findings_path", type=Path,
                   help="Path to the auditor's findings.json")
    args = p.parse_args()
    if not args.findings_path.exists():
        print(f"ERROR: {args.findings_path} does not exist", file=sys.stderr)
        return 1
    verify_findings(args.findings_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
