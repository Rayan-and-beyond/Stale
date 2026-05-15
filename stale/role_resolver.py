"""Free-text role resolver.

The user types something like "DevOps engineer focused on observability" or
"backend dev working on real-time systems". We need two things:

1. **Role enum** — one of the 9 values used to filter the postings dataset.
2. **Specialization hint** — a short phrase capturing the user's specific
   focus that gets injected into the Market-fit and Topics kickoff messages
   so those agents can weight their analysis appropriately.

A small Haiku 4.5 call does this. The call is one-shot (no caching benefit),
~$0.001, ~1–2s.

If parsing fails or the model is uncertain, we fall back to a closest-string
match against the enum and pass the raw input as the specialization. The
caller can present the resolved values back to the user before kicking off
Stage 2 if it wants confirmation UX.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

VALID_ROLES = [
    "backend", "frontend", "fullstack", "ml_data",
    "mobile", "devops_sre", "security", "embedded", "game",
]

ROLE_HINTS = {
    "backend":   "server-side APIs, databases, distributed systems",
    "frontend":  "browser UI, JS frameworks, design systems, accessibility",
    "fullstack": "both backend and frontend in one role",
    "ml_data":   "machine learning, data engineering, MLOps, analytics",
    "mobile":    "iOS / Android / cross-platform native apps",
    "devops_sre":"infrastructure, CI/CD, observability, reliability, cloud platform",
    "security":  "appsec, infosec, pentesting, red/blue team, cryptography",
    "embedded":  "firmware, RTOS, microcontrollers, hardware bring-up",
    "game":      "game engines, real-time rendering, gameplay systems",
}

_SYSTEM_PROMPT = (
    "You map a user's free-text career-target description onto one of these "
    "fixed role enums used to filter a job-postings dataset:\n\n"
    + "\n".join(f"  - {r}: {h}" for r, h in ROLE_HINTS.items())
    + "\n\nRespond with ONE JSON object and nothing else, in this schema:\n"
    "{\n"
    '  "role": "<one of the enums above, exactly>",\n'
    '  "specialization": "<short phrase (≤12 words) capturing the user\'s '
    'specific focus, or null if their input was generic like just \"backend\">",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "<one short sentence explaining the mapping>"\n'
    "}\n\n"
    "Pick the closest enum even if the fit is imperfect. The specialization "
    "is what's distinctive about the user's input that the generic enum "
    "loses — e.g. for 'DevOps engineer focused on observability' the role is "
    "'devops_sre' and the specialization is 'observability and instrumentation'. "
    "If the user's input is just a bare role name (e.g. 'backend'), set "
    "specialization to null."
)


def _extract_json(text: str) -> dict | None:
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced[0])
        except json.JSONDecodeError:
            pass
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
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _heuristic_fallback(free_text: str) -> dict:
    """If the Haiku call fails or returns garbage, use a substring match."""
    t = free_text.lower()
    table = [
        (["security", "appsec", "pentest", "red team", "blue team", "infosec"], "security"),
        (["devops", "sre", "platform", "infra", "kubernetes", "cloud engineer"], "devops_sre"),
        (["ml ", "machine learning", "data eng", "data scien", "ai engineer", "mlops"], "ml_data"),
        (["frontend", "front-end", "react", "ui engineer"], "frontend"),
        (["fullstack", "full-stack", "full stack"], "fullstack"),
        (["mobile", "ios", "android", "swift", "kotlin"], "mobile"),
        (["embedded", "firmware", "microcontroller", "rtos"], "embedded"),
        (["game", "unity", "unreal", "gameplay"], "game"),
        (["backend", "back-end", "server", "api"], "backend"),
    ]
    for keywords, role in table:
        if any(k in t for k in keywords):
            return {
                "role": role,
                "specialization": free_text.strip() if len(free_text.strip()) > len(role) + 2 else None,
                "confidence": "low",
                "reasoning": "heuristic substring match (Haiku call failed)",
            }
    return {
        "role": "backend",
        "specialization": free_text.strip() or None,
        "confidence": "low",
        "reasoning": "no keyword matched; defaulting to backend",
    }


def resolve_role(free_text: str) -> dict:
    """Map user free-text -> {role, specialization, confidence, reasoning}.

    Always returns a dict with `role` set to one of VALID_ROLES. Never raises
    for parse failures — falls back to a heuristic match.
    """
    free_text = (free_text or "").strip()
    if not free_text:
        return {
            "role": "backend",
            "specialization": None,
            "confidence": "low",
            "reasoning": "empty input; defaulting to backend",
        }

    load_dotenv(ENV_FILE)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _heuristic_fallback(free_text)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": free_text}],
        )
        text_blocks = [b.text for b in resp.content if getattr(b, "text", None)]
        parsed = _extract_json("\n".join(text_blocks))
    except Exception:
        return _heuristic_fallback(free_text)

    if not parsed or parsed.get("role") not in VALID_ROLES:
        return _heuristic_fallback(free_text)

    spec = parsed.get("specialization")
    if isinstance(spec, str):
        spec = spec.strip() or None
    return {
        "role": parsed["role"],
        "specialization": spec,
        "confidence": parsed.get("confidence", "medium"),
        "reasoning": parsed.get("reasoning", ""),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m stale.role_resolver '<free text>'")
        sys.exit(1)
    print(json.dumps(resolve_role(" ".join(sys.argv[1:])), indent=2))
