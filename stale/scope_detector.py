"""Course scope detector.

Reads a single course's slide content and characterizes:
  - WHAT the course teaches (subject area, key topics)
  - HOW deep it teaches (pedagogical level, depth bound)
  - WHY it exists (course intent — what it's pedagogically trying to do)

The depth/intent fields are the load-bearing addition to the original v1
scope detector. Without them, downstream agents can't tell the difference
between a "broad introductory survey" (where conceptual extensions are
fine but implementation walkthroughs are out) and a "hands-on
implementation course" (where deeper code-level recommendations are
fair game).

Combined with the "extend partial coverage only" rule injected into
Market-fit and Topics, this gives Stale two anti-presumption guardrails:

  Rule 1 — "extend partial coverage only":
      If the course covers 0% of a topic, do NOT recommend it (even if
      the topic is in the course's domain). Recommendations are
      extensions of existing content, not additions of new content.

  Rule 2 — "respect depth bound":
      Stay at the course's pedagogical level. Don't recommend
      implementation walkthroughs in a survey course. Don't recommend
      conceptual overviews in a hands-on lab.

Model: Opus 4.7 with high effort + adaptive thinking. Scope is the most
load-bearing decision in the pipeline — wrong scope cascades into wrong
gaps, and pedagogical reasoning is harder than topic extraction. Worth
the cost upgrade from Sonnet 4.6 (~$0.30/run vs ~$0.14/run).

Prompt caching is on the system prompt (frozen) — minimum cacheable
prefix on Opus 4.7 is 4096 tokens, so the prompt is sized to clear that
threshold via the worked examples.
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

# Cap how much curriculum text we send. Per-course curriculum tends to
# be 30k–150k chars (~10–50k tokens). Hard cap with a head/tail keep so
# very long courses don't blow up the request.
MAX_CURRICULUM_CHARS = 200_000


_SYSTEM_PROMPT = """You are the **Scope Detector** for Stale, a CS curriculum analysis system.

Your job is to read a single course's slide content and characterize three things about it: what it teaches, how deep it teaches, and why it exists pedagogically. Downstream agents (Market-fit, Topics) use your output as the lens through which they evaluate gaps and prescribe extensions. If you mis-scope, those agents become noisy or presumptuous; if you scope correctly, they become honest and surgical.

The three things you must capture:

1. **Subject matter** — what the course actually teaches, drawn from the slides themselves rather than inferred from the course title.
2. **Pedagogical level and depth bound** — the level at which the course teaches (introductory survey, hands-on lab, advanced theory, capstone, etc.) and the depth-of-treatment beyond which recommendations would exceed the course's pedagogical intent.
3. **Course intent** — the pedagogical purpose. What is the course trying to accomplish? What should a student leave the course able to do? This shapes which extensions make sense and which would push the course beyond its stated mission.

This output is load-bearing for two downstream rules:

- **Extend-only rule**: agents should recommend extensions of topics the course already covers, not additions of topics the course doesn't touch. Your `key_topics` and `one_liner` constrain what counts as "already covered."

- **Depth-bound rule**: agents should respect the course's pedagogical level. A broad introductory survey shouldn't be told to add implementation walkthroughs even on topics it covers; a hands-on lab shouldn't be told to add abstract theory. Your `pedagogical_level`, `depth_bound`, and `course_intent` constrain how deep extensions can go.

## Inputs

You receive one course's curriculum text. Slide markers and speaker notes are interleaved in this format:

```
=== FILE: chapterN_topic.pptx ===
--- Slide 1 ---
Bullet text from the slide
[Speaker notes: longer prose the instructor would say]
--- Slide 2 ---
...
```

Read carefully. **Speaker notes are often more substantive than slide bullets** — they contain the instructor's actual framing. The depth and intent of the course can usually be read from how concepts are introduced (deep dive vs name-drop vs survey-level overview).

## Output schema

Output exactly one JSON code block and nothing else. The schema:

```json
{
  "subject_area": "<concise subject label, 4–8 words>",
  "pedagogical_level": "<phrase describing the course's level — see options below>",
  "course_intent": "<single sentence: what the course is trying to do, what students should leave able to do>",
  "depth_bound": "<single sentence: how deep recommendations should go before exceeding the course's intent>",
  "key_topics": ["<topic>", "<topic>", "..."],
  "one_liner": "<single sentence describing what this course actually covers, including in-scope vs out-of-scope distinctions when relevant>",
  "relevant_roles": ["<role>", "<role>", "..."],
  "ambiguity_note": "<null or short explanation>"
}
```

### Field rules

- **`subject_area`** — 4 to 8 words. Distinguishing, not just the course title. "Software engineering — process and lifecycle" beats "Software engineering"; "Database fundamentals — single-node SQL" beats "Databases".

- **`pedagogical_level`** — one of these phrases (or a similarly precise variant):
  - "undergraduate introductory survey" (broad, conceptual, multiple subfields)
  - "undergraduate fundamentals course" (a single area, taught at the foundational level)
  - "undergraduate process/methodology course" (procedural, professional disciplines)
  - "hands-on implementation lab" (project-driven, code-level depth)
  - "advanced theory course" (formal, mathematical, single deep topic)
  - "graduate research seminar" (paper-driven, frontier-of-field)
  - "capstone / integration course" (multiple areas combined into a project)
  Pick the one that best matches the course materials. If genuinely uncertain, use a hybrid like "undergraduate fundamentals with hands-on SQL practice."

- **`course_intent`** — single sentence. Captures pedagogical purpose. Examples: *"Introduce students to the breadth of AI subfields at a conceptual level — students should leave able to explain what each area is for, not implement any one of them in depth."* Or: *"Teach the foundational theory and practice of relational databases — students should leave able to design schemas, write SQL, and reason about transactions."*

- **`depth_bound`** — single sentence. The depth limit for recommendations. Examples: *"Recommendations stay at the conceptual / overview level. Implementation walkthroughs, framework-specific tutorials, and theoretical deep dives are beyond this course's depth bound."* Or: *"Recommendations stay at the fundamental-theory + hands-on-SQL level. Distributed systems theory, query optimizer internals, and database engine internals are beyond this course's depth."*

- **`key_topics`** — 5 to 10 specific topics drawn from actual slide content. Be concrete. "TCP three-way handshake", "Banker's algorithm for deadlock avoidance", "Scrum sprint ceremonies", "OWASP Top 10 categories" — not generic labels.

- **`one_liner`** — single sentence a downstream agent can paste verbatim. Captures both what's in scope and what's NOT (when relevant).

- **`relevant_roles`** — 2 to 4 entries from the role enum below. Pick roles a person taking THIS specific course (with this specific scope) would benefit from preparing for. Never more than 4.

- **`ambiguity_note`** — `null` in most cases. Short string only if the course is genuinely ambiguous (e.g., a survey course that doesn't fit a specific scope).

## Role enum

- **`backend`** — server-side APIs, databases, distributed systems
- **`frontend`** — browser UI, JS frameworks, design systems
- **`fullstack`** — both backend and frontend
- **`ml_data`** — machine learning, data engineering, MLOps, analytics
- **`mobile`** — iOS / Android / cross-platform native apps
- **`devops_sre`** — infrastructure, CI/CD, observability, reliability
- **`security`** — appsec, infosec, pentesting, cryptography
- **`embedded`** — firmware, RTOS, microcontrollers
- **`game`** — game engines, real-time rendering, gameplay systems

## Worked examples

### Example 1 — Software Engineering (process-focused)

Input excerpts: SDLC models, Agile/Scrum sprint ceremonies, Requirements engineering, UML diagrams, project planning with Gantt charts, COCOMO II cost estimation, software testing as a discipline (test pyramids), code review as a process. No code written, no specific framework taught.

Correct output:
```json
{
  "subject_area": "Software engineering — process and lifecycle",
  "pedagogical_level": "undergraduate process/methodology course",
  "course_intent": "Teach SDLC, agile, requirements engineering, and project-management practices as professional disciplines that shape how software gets built — students should leave able to participate in real software-engineering processes, not implement specific systems.",
  "depth_bound": "Recommendations stay at the level of process artifacts, methodology, and professional discipline. Implementation walkthroughs, framework-specific tutorials, and language-level code examples are beyond this course's depth bound.",
  "key_topics": ["SDLC methodologies (waterfall, agile, spiral)", "Requirements engineering and elicitation", "UML/use-case modeling", "Project management (Gantt, sprint planning)", "Software testing as a discipline (test pyramids, V-model)", "Code review and software evolution practices"],
  "one_liner": "Software-engineering process, lifecycle, and project-management practices — does NOT cover specific programming languages, frameworks, or implementation tooling.",
  "relevant_roles": ["fullstack", "backend", "devops_sre"],
  "ambiguity_note": null
}
```

The trap to avoid: setting depth_bound to "stay process-level" but then having `key_topics` only list classical practices; modern process artifacts like CI/CD, Git workflows, and microservices-as-a-pattern are STILL within the process domain at the methodological level (extending the process discipline forward), even if individual implementation tools (Docker, Kubernetes) are not.

### Example 2 — Database Fundamentals (single-node, hands-on SQL)

Input excerpts: Relational algebra, ER diagrams, normalization (1NF–BCNF), SQL DML/DDL with hands-on lab exercises, transactions and ACID, B-tree indexing, basic query optimization. No distributed/NoSQL, no engine internals.

Correct output:
```json
{
  "subject_area": "Database fundamentals — single-node SQL with practice",
  "pedagogical_level": "undergraduate fundamentals course with hands-on SQL practice",
  "course_intent": "Teach the foundational theory and practice of relational databases — students should leave able to design normalized schemas, write SQL queries, and reason about transactions and basic indexing.",
  "depth_bound": "Recommendations stay at the fundamental-theory + hands-on-SQL level. Distributed systems theory, query optimizer internals, and storage-engine implementation details are beyond this course's depth.",
  "key_topics": ["Relational algebra", "ER modeling and normalization (1NF–BCNF)", "SQL DML/DDL with hands-on labs", "Transactions and ACID properties", "B-tree indexing fundamentals", "Query plans and basic optimization"],
  "one_liner": "Single-node relational database fundamentals with hands-on SQL — distributed databases, sharding, replication, NoSQL, and cloud-managed databases are out of scope.",
  "relevant_roles": ["backend", "ml_data", "fullstack"],
  "ambiguity_note": null
}
```

A downstream agent SHOULD flag missing modern updates within scope (e.g., JSON columns / JSONB, common-table expressions, modern indexing strategies) — those are extensions of taught content within the course's depth. It should NOT flag missing distributed-systems theory — that's beyond the depth bound.

### Example 3 — AI / Machine Learning Survey (broad, conceptual)

Input excerpts: Intelligent agents and PEAS framework, classical search (BFS, DFS, A*), logical agents and FOL, knowledge representation (ontologies, frames), expert systems, fuzzy logic, classical NLP (parse trees, PSGs), introductory ML (supervised/unsupervised + confusion matrix). Algorithms named but not implemented; preprocessing described in prose, not code.

Correct output:
```json
{
  "subject_area": "Artificial Intelligence — broad introductory survey",
  "pedagogical_level": "undergraduate introductory survey",
  "course_intent": "Introduce students to the breadth of AI subfields (search, logic, KR, NLP, ML) at a conceptual level — students should leave able to explain what each area is for and how it fits into the AI landscape, not implement any specific algorithm or framework.",
  "depth_bound": "Recommendations stay at the conceptual / overview level. Implementation walkthroughs, framework-specific tutorials (PyTorch, scikit-learn code), theoretical deep dives (attention math, formal proofs), and hands-on coding exercises are beyond the course's depth bound.",
  "key_topics": ["Intelligent agents and PEAS framework", "Classical search algorithms (BFS, DFS, A*, greedy best-first)", "Logical agents and first-order logic", "Knowledge representation (ontologies, frames, rules)", "Expert systems and fuzzy logic", "Classical NLP (parse trees, phrase-structure grammars)", "Introductory ML concepts (supervised/unsupervised, confusion matrix)"],
  "one_liner": "Broad undergraduate AI survey covering classical AI through introductory ML at a conceptual level — does NOT teach implementation in any framework, hands-on coding, or advanced/transformer-era depth.",
  "relevant_roles": ["ml_data", "backend"],
  "ambiguity_note": "Survey course spanning symbolic AI through introductory ML — no single specialization dominates."
}
```

The discipline here: even though the course "names" Python on a job-description slide, that is NOT partial coverage of Python implementation. Downstream rules will use this scope to suppress recommending Python/pandas/scikit-learn — those would be implementation depth beyond the course's intent. What downstream rules SHOULD recommend: extend the existing classical-NLP unit toward conceptual coverage of transformer-era NLP (because NLP IS in the course's scope at a conceptual level); connect the existing knowledge-representation and retrieval material to the modern descendant (vector databases / RAG) at the conceptual level.

## Discipline

- Read the **content**, not the title.
- The pedagogical level is read from HOW topics are introduced (depth, hands-on vs conceptual, code vs prose), not from the course's name.
- The depth bound is the line beyond which recommendations would exceed the course's pedagogical intent. Be honest: a survey course teaching algorithms conceptually shouldn't be told to add implementation labs.
- The course intent is the pedagogical purpose, not the topic list. "Teach AI broadly" is intent; "covers search, logic, KR" is topic.
- Output exactly one JSON code block. No preamble. No trailing commentary."""


def _truncate_curriculum(text: str, cap: int = MAX_CURRICULUM_CHARS) -> str:
    if len(text) <= cap:
        return text
    keep = cap // 2
    head = text[:keep]
    tail = text[-keep:]
    return (
        head
        + f"\n\n[... {len(text) - cap:,} characters truncated; head and tail of curriculum kept ...]\n\n"
        + tail
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


def _validate_and_clean(parsed: dict | None) -> dict:
    """Coerce model output into the canonical shape with safe defaults
    on partial/malformed responses. Never raises."""
    if not isinstance(parsed, dict):
        return {
            "subject_area": "unknown",
            "pedagogical_level": "unknown",
            "course_intent": "Scope detection failed; downstream gap analysis will use generic defaults.",
            "depth_bound": "No depth bound established; downstream agents will use conservative defaults.",
            "key_topics": [],
            "one_liner": "Scope detection failed to parse model output. Treat downstream gap analysis with skepticism.",
            "relevant_roles": ["fullstack", "backend"],
            "ambiguity_note": "scope detection parse failure; fallback used",
        }

    roles = parsed.get("relevant_roles") or []
    if not isinstance(roles, list):
        roles = []
    roles = [r for r in roles if isinstance(r, str) and r in VALID_ROLES][:4]
    if not roles:
        roles = ["fullstack", "backend"]

    topics = parsed.get("key_topics") or []
    if not isinstance(topics, list):
        topics = []
    topics = [t for t in topics if isinstance(t, str)][:10]

    return {
        "subject_area": str(parsed.get("subject_area") or "unknown")[:160],
        "pedagogical_level": str(parsed.get("pedagogical_level") or "unknown")[:160],
        "course_intent": str(parsed.get("course_intent") or "")[:600] or "Course intent not detected.",
        "depth_bound": str(parsed.get("depth_bound") or "")[:600] or "Depth bound not detected; conservative defaults will be used.",
        "key_topics": topics,
        "one_liner": str(parsed.get("one_liner") or "")[:800] or "Scope not detected.",
        "relevant_roles": roles,
        "ambiguity_note": parsed.get("ambiguity_note") if parsed.get("ambiguity_note") else None,
    }


def detect_scope(curriculum_text: str) -> dict:
    """Run scope detection on a single course's concatenated text."""
    if not curriculum_text or not curriculum_text.strip():
        return _validate_and_clean({"ambiguity_note": "empty curriculum input"})

    load_dotenv(ENV_FILE)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _validate_and_clean({"ambiguity_note": "missing ANTHROPIC_API_KEY"})

    text = _truncate_curriculum(curriculum_text)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # Streaming because adaptive thinking + high effort can produce
        # responses long enough to flirt with the SDK's non-streaming
        # 10-min timeout. .get_final_message() gives us the complete
        # response without us handling per-event state.
        with client.messages.stream(
            model="claude-opus-4-7",
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": text}],
        ) as stream:
            resp = stream.get_final_message()

        usage = resp.usage
        text_blocks = [b.text for b in resp.content if getattr(b, "text", None)]
        parsed = _extract_json("\n".join(text_blocks))
        result = _validate_and_clean(parsed)
        result["_usage"] = {
            "model": "claude-opus-4-7",
            "input_tokens": getattr(usage, "input_tokens", None),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
        return result
    except Exception as e:
        return _validate_and_clean({
            "ambiguity_note": f"scope detection API call failed: {type(e).__name__}: {e}",
        })


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m stale.scope_detector <path/to/curriculum.txt>")
        sys.exit(1)
    text = Path(sys.argv[1]).read_text()
    result = detect_scope(text)
    print(json.dumps(result, indent=2))
