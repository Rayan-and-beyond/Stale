"""Deterministic slide_ref verifier.

The adjudicator can mis-attribute a `slide_ref` (e.g. write Lecture 01 when the
verbatim `claim` actually lives in Lecture 02). The two-stage pipeline already
demands `claim` be byte-verbatim slide text; we exploit that here:

  - search the concatenated curriculum text for each finding's `claim`
  - when found, identify the `=== FILE: ===` block and `--- Slide N ---` /
    `--- Page N ---` marker the match falls under
  - rewrite `slide_ref` to the locations the claim actually appears at

Findings whose `claim` cannot be found anywhere — even after normalizing
whitespace and smart quotes — keep their original `slide_ref` and are tagged
`slide_ref_verification.status = "not_found"` for downstream UI/reviewer
visibility.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Match either "--- Slide N ---" (pptx) or "--- Page N ---" (pdf).
_SLIDE_RE = re.compile(r"^---\s*(Slide|Page)\s+(\d+)\s*---\s*$", re.MULTILINE)
_FILE_RE = re.compile(r"^===\s*FILE:\s*(.+?)\s*===\s*$", re.MULTILINE)
_CODELIKE_CHARS = set("{}();=<>[]")
_CANDIDATE_TEXT_FIELDS = (
    "snippet",
    "context_before",
    "context_after",
    "prima_facie_concern",
)


def _normalize(s: str) -> str:
    """Collapse whitespace and ASCII-fold quotes/dashes for tolerant matching."""
    s = unicodedata.normalize("NFKC", s)
    # Smart quotes / dashes → ASCII
    s = (s.replace("‘", "'").replace("’", "'")
           .replace("“", '"').replace("”", '"')
           .replace("–", "-").replace("—", "-"))
    # Collapse all whitespace runs to single spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_index(course_text: str) -> list[tuple[int, int, str, int]]:
    """Return [(start_offset, end_offset, file_name, slide_or_page_number)]
    for every slide/page block in the concatenated course text."""
    files: list[tuple[int, str]] = [
        (m.start(), m.group(1)) for m in _FILE_RE.finditer(course_text)
    ]
    slides: list[tuple[int, int]] = [
        (m.start(), int(m.group(2))) for m in _SLIDE_RE.finditer(course_text)
    ]

    blocks: list[tuple[int, int, str, int]] = []
    for i, (s_start, s_num) in enumerate(slides):
        s_end = slides[i + 1][0] if i + 1 < len(slides) else len(course_text)
        # find the file this slide belongs to (last `=== FILE ===` before s_start)
        owning_file = "?"
        for f_start, fname in files:
            if f_start <= s_start:
                owning_file = fname
            else:
                break
        blocks.append((s_start, s_end, owning_file, s_num))
    return blocks


def _kind_label(filename: str) -> str:
    """Pptx → 'slide', pdf → 'page'."""
    return "page" if filename.lower().endswith(".pdf") else "slide"


def _looks_like_sentence_fragment(claim: str) -> bool:
    """Return True for prose claims that are likely truncated mid-sentence.

    We deliberately avoid code-like claims: expanding code anchors can turn a
    precise bad line into a huge expression, while prose prefixes such as
    "Finalize is used to perform" look broken in the UI.
    """
    stripped = claim.strip()
    if len(stripped) < 16:
        return False
    if any(ch in stripped for ch in _CODELIKE_CHARS):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", stripped)
    return len(words) >= 4


def _candidate_lines(candidates: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for candidate in candidates.get("candidates", []):
        for field in _CANDIDATE_TEXT_FIELDS:
            text = candidate.get(field, "") or ""
            lines.extend(ln.strip() for ln in text.splitlines() if ln.strip())
    return lines


def expand_short_claims(
    findings: list[dict[str, Any]],
    candidates: dict[str, Any],
) -> dict[str, Any]:
    """Expand prose claims that are prefixes of fuller extractor snippets.

    The adjudicator sometimes chooses a byte-verbatim prefix that passes
    slide_ref verification but reads as a cut-off sentence. The extractor
    candidate usually has the complete line, so we can repair that determin-
    istically before saving findings.
    """
    lines = _candidate_lines(candidates)
    expanded = 0

    for finding in findings:
        claim = (finding.get("claim") or "").strip()
        if not _looks_like_sentence_fragment(claim):
            continue

        matches = [
            line for line in lines
            if line.startswith(claim) and len(line) >= len(claim) + 12
        ]
        if not matches:
            continue

        replacement = min(matches, key=len)
        finding["claim"] = replacement
        finding["claim_expansion"] = {
            "status": "expanded",
            "original": claim,
        }
        expanded += 1

    return {
        "total": len(findings),
        "expanded": expanded,
    }


def verify_slide_refs(
    findings: list[dict[str, Any]],
    courses: dict[str, str],
) -> dict[str, Any]:
    """Mutate findings in place to correct `slide_ref`. Returns a summary dict.

    `courses` is the same {course_name: concatenated_text} dict the agents
    received (output of stale.tools.extract.extract_curriculum).
    """
    # Index every (file, slide/page, normalized_text) block once
    indexed: list[tuple[str, str, int, str]] = []  # (course, file, slide_num, normalized)
    for course_name, full_text in courses.items():
        for s_start, s_end, fname, s_num in _build_index(full_text):
            block_text = full_text[s_start:s_end]
            indexed.append((course_name, fname, s_num, _normalize(block_text)))

    matched = 0
    rewritten = 0
    not_found = 0
    for f in findings:
        claim = f.get("claim", "") or ""
        original_ref = f.get("slide_ref", "")
        if not claim.strip():
            f["slide_ref_verification"] = {"status": "no_claim",
                                           "original": original_ref}
            not_found += 1
            continue

        # Anchor on a strong sub-phrase: the longest line, or first 80 chars.
        lines = [ln.strip() for ln in claim.splitlines() if ln.strip()]
        anchor = max(lines, key=len) if lines else claim
        if len(anchor) > 120:
            anchor = anchor[:120]
        norm_anchor = _normalize(anchor)
        if not norm_anchor:
            f["slide_ref_verification"] = {"status": "no_claim",
                                           "original": original_ref}
            not_found += 1
            continue

        hits: list[tuple[str, str, int]] = []  # (course, file, slide_num)
        for course_name, fname, s_num, norm_block in indexed:
            if norm_anchor in norm_block:
                hits.append((course_name, fname, s_num))

        if not hits:
            f["slide_ref_verification"] = {"status": "not_found",
                                           "original": original_ref,
                                           "anchor_used": anchor}
            not_found += 1
            continue

        # Build the comma-separated authoritative slide_ref.
        # Within a single file, sort by slide number.
        hits_sorted = sorted(set(hits), key=lambda x: (x[1], x[2]))
        new_ref = ", ".join(
            f"{fname} {_kind_label(fname)} {s_num}"
            for _course, fname, s_num in hits_sorted
        )
        f["slide_ref_verification"] = {
            "status": "matched" if new_ref == original_ref else "rewritten",
            "original": original_ref,
        }
        if new_ref != original_ref:
            f["slide_ref"] = new_ref
            rewritten += 1
        else:
            matched += 1

    return {
        "total": len(findings),
        "matched": matched,
        "rewritten": rewritten,
        "not_found": not_found,
    }
