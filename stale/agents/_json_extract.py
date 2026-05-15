"""Tolerant JSON-block extraction from agent output.

Agent text usually ends with a fenced ```json ... ``` block, but agents
occasionally emit a string value containing an unescaped `"word"` —
e.g. "...changes have not "broken" previously working code..." — which
breaks `json.loads`. We retry by escaping the most likely stray quote
and parsing again, capped at a sensible iteration count.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", flags=re.DOTALL)
_GREEDY_FENCE_RE = re.compile(r"```json\s*(\{.*\})\s*```", flags=re.DOTALL)


def _repair_once(text: str, pos: int) -> str | None:
    """Try to escape the stray internal `"` nearest to `pos` and return the
    repaired text. None if no plausible candidate found.

    We look for `"` characters that are surrounded by alphanumerics — i.e.
    look like word boundaries inside a sentence — and escape the closest
    one preceding the parser's failure position."""
    window_start = max(0, pos - 4000)
    for i in range(pos, window_start, -1):
        if text[i:i + 1] != '"':
            continue
        before = text[i - 1:i]
        after = text[i + 1:i + 2]
        # Skip already-escaped quotes.
        if text[i - 1:i] == "\\":
            continue
        # Heuristic: stray quotes are flanked by letters or by a letter on one
        # side and a non-structural char on the other. Real string delimiters
        # are flanked by ',', ':', '[', ']', '{', '}', or whitespace.
        if before.isalpha() and (after.isalpha() or after in (" ", ".", ",", ";", ")")):
            return text[:i] + "\\" + text[i:]
        if after.isalpha() and before in (" ", "(", ".", ",", ";"):
            return text[:i] + "\\" + text[i:]
    return None


def extract_json_block(text: str, max_repairs: int = 50) -> dict[str, Any] | None:
    """Pull the JSON object from agent output. Returns the parsed dict or None.

    Strategy:
      1. Look for a fenced ```json {...} ``` block (non-greedy, then greedy).
      2. Try `json.loads`. On failure, attempt iterative quote-escape repair.
      3. Fall back to the first balanced top-level `{...}` substring.
    """
    candidates: list[str] = []
    fenced = _FENCE_RE.findall(text)
    if fenced:
        candidates.append(fenced[-1])
    greedy = _GREEDY_FENCE_RE.search(text)
    if greedy and greedy.group(1) not in candidates:
        candidates.append(greedy.group(1))

    for body in candidates:
        result = _try_parse_with_repair(body, max_repairs)
        if result is not None:
            return result

    # Fallback: scan for first balanced object.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                result = _try_parse_with_repair(text[start: i + 1], max_repairs)
                if result is not None:
                    return result
                break
    return None


def _try_parse_with_repair(body: str, max_repairs: int) -> dict[str, Any] | None:
    current = body
    for _ in range(max_repairs):
        try:
            return json.loads(current)
        except json.JSONDecodeError as e:
            repaired = _repair_once(current, e.pos)
            if repaired is None or repaired == current:
                return None
            current = repaired
    return None
