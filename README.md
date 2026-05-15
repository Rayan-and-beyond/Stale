# Stale

**A multi-agent system that audits CS course materials for outdated, deprecated, insecure, or factually wrong content — and grounds every finding in a primary-source citation.**

Stale reads slide decks (`.pdf` / `.pptx`, with speaker notes and OCR for code screenshots), runs a two-stage agent pipeline against them, and produces a structured report you can click through in a web UI. It also compares what a course *actually teaches* against role-tagged job-market demand and ranks the missing topics.

> Built with Anthropic's Beta Managed Agents API. Two Claude models in production: **Sonnet 4.6** for high-recall candidate surfacing, **Opus 4.7** for final flag/skip judgment with primary-source citations. Deterministic Python verifiers re-fetch every cited URL, substring-match the agent's quoted excerpt, and re-anchor every `slide_ref` against the course text — catching fabrications and mis-attribution at zero LLM cost.

---

## What it does

Given a folder of course slide decks and a target job role, Stale produces three artifacts:

1. **Audit** — every code snippet, claim, library mention, or syntax pattern in the curriculum that is dead / deprecated / won't compile / breaks at runtime / outdated teaching / conceptually wrong. Each finding carries:
   - The verbatim slide quote (grep-checkable against the PDF)
   - A category from a 7-bucket taxonomy
   - A primary-source citation (Oracle docs, JLS sections, JEPs/PEPs, RFCs, CVEs, MDN)
   - A re-fetched-and-substring-matched citation excerpt
   - A "what to teach instead" suggested replacement
   - An audit-trail note explaining why this wasn't a counterexample

2. **Market-fit gap analysis** — extracts the actual skillset taught (not just course titles), compares it against role-tagged job postings, returns gaps with severity, market-frequency percentages, and posting citations. Strict-scope: only flags gaps the course already partially covers.

3. **Topics ranking** — converts gaps into prescriptions ordered by market frequency, with prerequisite chains and "where it fits in the existing curriculum" recommendations.

## Architecture

```
                ┌─────────────────────────────────┐
                │   slide decks (.pdf / .pptx)    │
                └────────────────┬────────────────┘
                                 │  PDF + PPTX text extraction
                                 │  (pymupdf, python-pptx, OCR fallback)
                                 ▼
            ┌──────────────────────────────────────┐
            │       Audit pipeline (two stage)     │
            │                                       │
            │   ┌─────────────┐    ┌────────────┐  │
            │   │  Extractor  │───▶│Adjudicator │  │
            │   │ (Sonnet 4.6)│    │ (Opus 4.7) │  │
            │   │             │    │            │  │
            │   │  high recall│    │ FLAG/SKIP  │  │
            │   │  candidates │    │ + cite     │  │
            │   └─────────────┘    └────────────┘  │
            │            │              │          │
            │            ▼              ▼          │
            │     candidates.json   findings.json  │
            └──────────────────────────────────────┘
                                 │
                                 │  Deterministic Python verifiers
                                 │  • slide_ref re-anchored against curriculum
                                 │  • citation excerpt substring-matched on URL
                                 ▼
            ┌──────────────────────────────────────┐
            │      Market-fit + Topics (parallel)  │
            │                                       │
            │   ┌─────────────┐    ┌────────────┐  │
            │   │ Market-fit  │───▶│   Topics   │  │
            │   │ (Opus 4.7)  │    │ (Opus 4.7) │  │
            │   └─────────────┘    └────────────┘  │
            │       gaps.json       prescriptions  │
            └──────────────────────────────────────┘
                                 │
                                 ▼
                    FastAPI + Jinja2 web UI
```

Detailed design: [`docs/architecture.md`](docs/architecture.md).
Decision history: [`docs/decisions.md`](docs/decisions.md).

## Why two stages

The original auditor was a single Opus session that decided FLAG vs SKIP inline as it read the curriculum. It silently rationalized away real deprecations as "probably a counterexample" — high precision, terrible recall.

Splitting into **Extractor** (high-recall: surface every candidate, do not decide) → **Adjudicator** (per-candidate FLAG/SKIP with full course context and burden-of-proof on SKIP) was the architectural fix. Validated on two real courses (OOP 1, OOP 2) with reviewer-graded output:

"Findings (verified)" is the subset that survives deterministic citation re-fetch + slide_ref re-anchoring — what the UI shows. The Adjudicator's raw output (`findings.json`) is larger; the verified subset is in `findings_kept.json`.

| Course | Files | Candidates | Adjudicator FLAGs | Skipped | Findings (verified) | Categories used |
|---|---:|---:|---:|---:|---:|:--|
| OOP 1 | 8 | 25 | 20 | 5 | 10 | dead · doesnt_compile · runtime_wrong · deprecated · conceptual |
| OOP 2 | 11 | 37 | 9 | 25 | 8 | dead · deprecated · outdated_teaching · conceptual |

## Quick start

```bash
# 1. clone + install (editable install — see "Installation" below)
git clone <this-repo>
cd Stale
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. configure your Anthropic API key
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
# requires Anthropic API access with Managed Agents beta enabled

# 3. one-time agent setup (creates your 4 Managed Agents on Anthropic's side
#    and writes their IDs back into your local .env)
python -m stale.setup_agents
# safe to rerun: it exits without creating new agents unless you pass --force

# 4. run the web UI
python -m stale.web
# open http://127.0.0.1:8000
```

Do not commit `.env`; it contains your API key and your own Managed Agent IDs. Drop a folder of `.pdf` / `.pptx` slide decks via the upload form, pick a target role, then review Findings, Market-fit, and Topics in the run page. **No API key?** The repo ships with 11 curated demo runs visible in the UI on a fresh clone.

```bash
# run the test suite (26 tests)
pytest
```

CLI mode (skip the web UI) is also supported for the audit pipeline:

```bash
python -m stale.agents.auditor --curriculum-dir curriculum/ --courses "Object Oriented Programming 1"
```

### Installation

`pip install -e .` is the supported install. The Python package, prompts, web templates and static assets all ship via `[tool.setuptools.package-data]`, so a wheel install renders existing demo runs correctly. Generating *new* runs additionally needs `data/job_postings.json` at repo root (a synthetic dataset built by `data/build_postings.py`); that's the one path that requires a clone, not a wheel install.

## Cost

Caching is automatic on Managed Agents. A typical end-to-end run:

| Phase | Model | Tokens read from cache | Cost / course |
|---|---|---:|---:|
| Extractor | Sonnet 4.6 | 9.6 M | ~$5 |
| Adjudicator | Opus 4.7 | 8.9 M | ~$25 |
| Market-fit | Opus 4.7 | — | ~$5 |
| Topics | Opus 4.7 | — | ~$3 |

90% input-rate discount on cached tokens, so input cost is dominated by the small uncached fraction. Adjudicator sits on Opus because that's where judgment quality lives.

## Repo structure

```
stale/
├── agents/
│   ├── auditor.py              # Two-stage pipeline (extractor → adjudicator)
│   ├── market_fit.py           # Skillset extraction + gap analysis
│   ├── topics.py               # Gap → prescription ranking
│   ├── _slide_ref_verifier.py  # Deterministic slide-attribution check
│   └── _json_extract.py        # Tolerant JSON parser for agent output
├── prompts/
│   ├── extractor.md            # ~7k chars — high-recall candidate surfacing
│   ├── adjudicator.md          # ~18k chars — FLAG/SKIP + citation discipline
│   ├── market_fit.md
│   └── topics.md
├── web/                        # FastAPI + Jinja2 UI
├── tools/extract.py            # PDF + PPTX + OCR text extraction
├── verify.py                   # Re-fetch citation URLs, substring-match excerpts
├── upload.py                   # Files API curriculum upload
├── role_resolver.py            # Free-text role → role profile (Haiku)
├── scope_detector.py           # Course → subject area detection (Sonnet)
└── setup_agents.py             # One-time: create the Managed Agents on Anthropic
data/
├── job_postings.json           # 108 synthetic role-tagged postings (SEED=42)
├── build_postings.py           # Frequency-calibrated generator
└── README.md                   # Methodology + calibration sources
docs/                           # Architecture, decisions, build plan, budget
```

## On the synthetic job-postings dataset

The market-fit agent reads from a deterministic local dataset (`SEED=42`), not live scraped postings. Per-skill frequencies are calibrated against named public surveys (Stack Overflow Developer Survey, GitHub Octoverse, LinkedIn Emerging Jobs, HackerRank, JetBrains). See [`data/README.md`](data/README.md) for full methodology and rationale.

This is a deliberate choice: live scraping LinkedIn / Indeed is fragile and ToS-questionable; the differentiator here is the *system architecture*, not where the rows came from. The `.invalid` TLD on every posting URL flags them as non-resolvable so no downstream consumer can mistake them for real listings.

## Run another role (no extra audit cost)

Once a course has been audited, you can re-run it against a different target role from its run page — the pipeline reuses the existing audit findings and only re-runs market-fit + topics. A typical retest takes ~15 minutes and skips the most expensive stage entirely. Look for the "Run another role" card on any completed run page; the new run links back to its source.

## Status

Working end-to-end on 11 (course, role) pairs across 8 distinct courses. Three reviewer passes plus a feature drop for role re-testing landed 2026-05-06/07 — see Decisions 12, 13, 14, 15. Pre-ship hardening covered red tests, false-success status pill, Auditor → Market-fit consistency wiring, package-data declarations, shared SSE reconnect helper, ghost-run filter on the index, and repo-wide secrets redaction with a regression scan in CI. Detection quality reviewer-rated shippable on the Mobile run (hardcoded keys, SQL injection, AsyncTask, deprecated fragments/loaders, C2DM, Dalvik, `MODE_WORLD_*` all caught with matched citations). See [`STATE.md`](STATE.md) for the current truth and [`docs/decisions.md`](docs/decisions.md) for the running design log (15 entries).

## License

MIT — see [`LICENSE`](LICENSE).
