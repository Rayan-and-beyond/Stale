# Stale

**Audit your CS curriculum for what's gone stale — and see what the job market is asking for that your school isn't teaching.**

**Website:** [getstale.dev](https://getstale.dev) · **Contact:** [rayan@getstale.dev](mailto:rayan@getstale.dev) · **Founder:** [Rayan Alhusennan](https://www.linkedin.com/in/rayan-alhusennan/)

Drop in slide decks from a university course, pick a target job role, and Stale tells you:
(1) what's outdated, deprecated, insecure, or just plain wrong, and corrects it.
(2) where the curriculum diverges from what employers in that role actually hire for.

> Built on Anthropic's Managed Agents API with **Sonnet** for high-recall candidate surfacing and **Opus** for final flag/skip judgment. A deterministic Python pass re-fetches every cited URL and re-anchors every slide reference against the course text, so unsupported findings are discarded before they reach the report.

---

## Where Stale is going

The audit engine is the first layer. Stale is being expanded into a persistent academic companion that can understand a student's degree from first year to graduation: courses, prerequisites, syllabi, schedules, and how knowledge connects across semesters.

The goal is to make a degree compound. Through GitHub, Stale can help students carry projects forward instead of abandoning them after each course: a database project can become the backend of a later web application, then become the system hardened in an application-security course. Over time, Stale can build study plans, surface prerequisite gaps, keep course material current, and expose academic context through a CLI and MCP server for personal agents.

## What you get back

Three reports, generated end-to-end from your slide decks:

1. **An audit of what's broken.** Every code snippet, claim, library mention, or syntax pattern in the curriculum that's dead, deprecated, won't compile today, breaks at runtime, teaches an outdated mental model, or is conceptually wrong. Each finding carries:
   - The verbatim slide quote (grep-checkable against the PDF)
   - A category from a 7-bucket taxonomy
   - A primary-source citation (Oracle docs, JLS sections, JEPs/PEPs, RFCs, CVEs, MDN)
   - A re-fetched-and-substring-matched citation excerpt
   - A "what to teach instead" suggested replacement
   - An audit-trail note explaining why this wasn't a counterexample

2. **A market-fit gap analysis.** Stale extracts the *actual* skillset the course teaches (not just titles on the schedule), compares it against role-tagged job postings, and returns gaps with severity, market-frequency percentages, and posting citations. Strict-scope: it only flags gaps the course already partially covers — never inventing topics out of thin air.

3. **A topics ranking.** Gaps converted into prescriptions, ordered by how often the market mentions them, with prerequisite chains and recommendations for where each one fits into the existing curriculum.

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
            │   │ (Sonnet)│    │ (Opus) │  │
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
            │   │ (Opus)  │    │ (Opus) │  │
            │   └─────────────┘    └────────────┘  │
            │       gaps.json       prescriptions  │
            └──────────────────────────────────────┘
                                 │
                                 ▼
                    FastAPI + Jinja2 web UI
```

Detailed design: [`docs/architecture.md`](docs/architecture.md).
Decision history: [`docs/decisions.md`](docs/decisions.md).

## Engineering decisions

### Separate recall from judgment

The first version was a single Opus session that read the slides and decided FLAG-vs-SKIP inline. It silently rationalized real deprecations away as "probably a counterexample" — high precision, terrible recall.

The fix was to split recall from judgment. **Extractor** (Sonnet) surfaces every candidate without deciding anything; **Adjudicator** (Opus) judges each candidate one at a time, with the full course as context and burden-of-proof on SKIP. Validated on two real courses (OOP 1, OOP 2) with reviewer-graded output:

| Course | Files | Candidates | Adjudicator FLAGs | Skipped | Findings (verified) | Categories used |
|---|---:|---:|---:|---:|---:|:--|
| OOP 1 | 8 | 25 | 20 | 5 | 10 | dead · doesnt_compile · runtime_wrong · deprecated · conceptual |
| OOP 2 | 11 | 37 | 9 | 25 | 8 | dead · deprecated · outdated_teaching · conceptual |

### Verify every citation outside the model

Every flagged claim passes through deterministic Python verification. Stale re-fetches the source URL, substring-matches the quoted excerpt, and re-anchors the slide reference against the original course text. If the evidence is missing, the finding never reaches the report.

The "Findings (verified)" column above is what the UI actually shows. The Adjudicator's raw output is kept in `findings.json`; the verified subset is written to `findings_kept.json`.

### Keep market-fit recommendations in scope

The Market-fit Agent can only flag gaps the course already partially covers. It cannot invent missing topics from nothing. This keeps the report tied to the curriculum instead of turning it into an unlimited wish list no syllabus could satisfy.

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
| Extractor | Sonnet | 9.6 M | ~$5 |
| Adjudicator | Opus | 8.9 M | ~$25 |
| Market-fit | Opus | — | ~$5 |
| Topics | Opus | — | ~$3 |

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

## Run another role

Once a course is audited, you can re-run it against a different target job role from its run page. The audit findings are reused and only the market-fit + topics stages re-fire — about 15 minutes per retest, skipping the most expensive part of the pipeline entirely. Look for the "Run another role" card on any completed run page.

## Status

**v1 shipped 2026-05-15.** Working end-to-end on 11 curated (course, role) runs across 10 distinct CS courses — all browsable at [getstale.dev/start](https://getstale.dev/start) and in the UI on a fresh clone without an API key. The Mobile run is the strongest demo of detection quality: hardcoded keys, SQL injection, AsyncTask, deprecated fragments/loaders, C2DM, Dalvik, and `MODE_WORLD_*` were all caught with matched primary-source citations. See [`STATE.md`](STATE.md) for the current truth and [`docs/decisions.md`](docs/decisions.md) for the 15-entry design log.

## License

MIT — see [`LICENSE`](LICENSE).
