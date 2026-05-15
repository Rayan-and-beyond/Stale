# Stale — Architecture

## What it is

A multi-agent system that ingests a CS student's full degree curriculum (slide decks, syllabi, assignments) and produces:

1. **An audit** of outdated, deprecated, insecure, or factually wrong content — every claim cited to primary sources.
2. **A market-fit gap analysis** — extracts the actual skillset taught (not just course titles), compares it against current job-market data for a chosen role, identifies gaps with quantitative evidence.
3. **Ranked topic remediations** — specific missing skills the student needs to add, ranked by job-posting frequency, with citations.

## Three agents, one pipeline

```
Orchestrator (Python driver — input: curriculum_dir, target_role)
├── Auditor       (parallel)              → outdated content + citations
├── Market-fit    (parallel, role-scoped) → skillset extraction + gap analysis
└── Topics        (sequential, consumes market-fit, role-scoped)
                                          → ranked missing skills
                                     ↓
                            Unified visual report
```

**Parallel:** Auditor and Market-fit have no dependency on each other — they read the same curriculum and run concurrently.

**Sequential:** Topics consumes Market-fit's gap output. It cannot start until Market-fit completes.

## Why per-feature, not per-course

We do **not** spin up a separate agent per course. With ~30 courses in a 4-year CS degree, that would mean 30+ sessions, 30+ containers, and duplication of audit/market-fit logic across each. Cost and orchestration explode for zero gain.

Instead, each agent specializes by *lens* (audit, market-fit, topics) and iterates over all courses internally inside a single session. Per-feature is vertical; per-course is horizontal. We pick vertical.

## Why the role parameter scopes output, not input

Input is **always universal** — every CS course the user has goes into the auditor and market-fit. The pitch is "drop your entire degree and find out what it taught you." Cherry-picking input courses kills that.

The `target_role` parameter (e.g., `backend`, `frontend`, `ml_data`, `mobile`, `devops`, `security`, `embedded`, `game`, `fullstack`) **only filters the lens** through which findings are interpreted:

- **Auditor** ignores the role (outdated is outdated regardless of career).
- **Market-fit** filters its job-postings dataset to the chosen role for the comparison.
- **Topics** ranks missing skills against role-specific demand.

This makes the output sharper and personally relevant without compromising the "your full degree" framing.

## Each agent in detail

### Agent 1: Auditor

| | |
|---|---|
| Model | `claude-opus-4-7` |
| Effort | `xhigh` |
| Thinking | adaptive, summarized |
| Tools | `agent_toolset_20260401` (read/grep/glob), `web_fetch`, `web_search` |
| Mounted resources | All extracted curriculum text |
| Role-aware | No |

**System prompt focus:** "You audit course materials. For every claim, tool, library, syntax, or pattern shown, verify against current authoritative sources (language specs, PEPs, CVE DB, official docs). Output structured findings with primary-source citations."

**Output schema:**
```json
[
  {
    "course": "Operating Systems",
    "slide_ref": "ch5_CPU_Scheduling.pptx slide 14",
    "claim": "Round-robin uses ...",
    "status": "outdated|wrong|insecure|dead",
    "severity": "low|medium|high|critical",
    "citation_url": "https://...",
    "suggested_replacement": "..."
  }
]
```

### Agent 2: Market-fit

| | |
|---|---|
| Model | `claude-opus-4-7` |
| Effort | `xhigh` |
| Thinking | adaptive, summarized |
| Tools | `agent_toolset_20260401`, custom `query_market_data`, `web_fetch` |
| Mounted resources | Curriculum + pre-fetched role-tagged job postings |
| Role-aware | Yes (filters posting dataset) |

**System prompt focus:** "Extract the actual skills/tools/libraries taught (not just course titles). Compare against current job market data for the target role. Identify gaps with quantitative evidence."

**Output schema:**
```json
{
  "taught_skillset": [{"skill": "...", "courses": [...]}],
  "market_demand":   [{"skill": "...", "frequency_pct": 0.89, "posting_refs": [...]}],
  "gaps":            [{"skill": "...", "market_frequency_pct": 0.89, "evidence_urls": [...]}]
}
```

### Agent 3: Topics (sequential, downstream of Market-fit)

| | |
|---|---|
| Model | `claude-opus-4-7` |
| Effort | `high` (lighter than analyzers) |
| Thinking | adaptive |
| Tools | `agent_toolset_20260401`, `web_fetch` |
| Resources | Market-fit's gap output passed as initial user message |
| Role-aware | Yes (ranks against role demand) |

**System prompt focus:** "Given a market-fit gap analysis, prescribe the specific missing topics ranked by job-posting frequency for the target role. Each prescription cites which postings demand it and which existing course it should map into."

**Output schema:**
```json
[
  {
    "topic": "...",
    "rank": 1,
    "market_frequency_pct": 0.89,
    "citations": [...],
    "fits_in_course": "Databases",
    "prerequisite_chain": [...]
  }
]
```

## Data flow + orchestration

```python
def run_stale(curriculum_dir, target_role):
    file_ids = upload_curriculum(curriculum_dir)            # PDF/PPTX → text → Files API

    audit_session  = start_session(AUDITOR_AGENT_ID, file_ids)
    market_session = start_session(MARKET_FIT_AGENT_ID, file_ids + [postings_file_id])

    audit_findings, market_gaps = await_parallel(
        run_session(audit_session,  initial=f"role={target_role}"),
        run_session(market_session, initial=f"role={target_role}"),
    )

    topics_session = start_session(TOPICS_AGENT_ID, [],
        initial=f"role={target_role}\ngaps={market_gaps}")
    topic_prescriptions = run_session(topics_session)

    return render_report(audit_findings, market_gaps, topic_prescriptions, target_role)
```

Stream-first ordering on every session: open SSE stream **before** sending the initial user message.

## Pre-fetched assets (version-controlled in `data/`)

| File | Purpose |
|---|---|
| `data/job_postings.json` | ~100 role-tagged postings (HN "Who's hiring" + LinkedIn snapshot) |
| `data/dev_survey.json` | Stack Overflow Developer Survey extract |
| `data/octoverse.json` | GitHub language/framework trends |
| `data/deprecations.json` | Curated dead libs, deprecated syntax, CVE classes |
| `data/skill_to_tool.json` | Skill → industry-current-tool rubric (~200 entries) |

When agents cite "47 of 50 postings demand X," the underlying postings exist on disk and are reproducible. No live scraping during demo runs.

## Custom tools (host-side)

Kept lean. Most navigation handled by `agent_toolset_20260401`. We add:

- `query_market_data(skill, role)` — reads pre-fetched postings, returns frequency + example refs.

PDF/PPTX extraction happens **upfront in the orchestrator** (extract once, upload as `.txt` files via Files API). Agents work on extracted text, not raw decks. Faster, cheaper, more reliable.

## Citation verification (post-Auditor, deterministic)

The Auditor is instructed to cite primary sources, but instruction is not proof. After the Auditor session ends, `stale/verify.py` runs a deterministic Python pass over `findings.json`:

1. `httpx.GET` every `citation_url` (HTML → strip tags; PDF → PyMuPDF).
2. Normalize whitespace + case on both the page text and the `citation_excerpt`.
3. Substring-check the excerpt. Findings that fail (URL dead, excerpt absent, content type unhandleable) are dropped from the kept set.

This catches the most common LLM failure mode (manufactured URLs / fabricated quotes) without spending another agent's tokens. It does **not** judge whether the `suggested_replacement` itself reflects current best practice — that is a deliberate scope cut. If smoke runs show squishy replacements, a Phase 4 Verifier agent (web_search-equipped) is the upgrade path; we don't add it speculatively.

Outputs: `verified.json` (every finding annotated) + `findings_kept.json` (only verified findings — this is what the report renders).

## Repo layout

```
Stale/
├── README.md                   # 100-200 word summary + run instructions
├── pyproject.toml
├── .env.example                # ANTHROPIC_API_KEY
├── stale/
│   ├── orchestrator.py         # Main entry — coordinates the 3 sessions
│   ├── setup_agents.py         # ONE-TIME: agents.create() × 3
│   ├── agents/
│   │   ├── auditor.py
│   │   ├── market_fit.py
│   │   └── topics.py
│   ├── tools/
│   │   ├── extract.py          # PDF + PPTX → text
│   │   └── market_query.py
│   ├── prompts/
│   │   ├── auditor.md
│   │   ├── market_fit.md
│   │   └── topics.md
│   └── report/
│       ├── render.py
│       └── template.html
├── data/                       # Pre-fetched, version-controlled
├── curriculum/                 # User's slides (gitignored or sample-only)
├── docs/                       # This directory
├── demo/
│   └── recording_script.md
└── tests/
    └── smoke.py
```
