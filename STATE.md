# Stale — Project State (last updated 2026-05-06)

> Quick-start hand-off doc. Read this first if you're picking up the project. Design docs live in [`docs/`](docs/); decision history in [`docs/decisions.md`](docs/decisions.md).

## TL;DR

A multi-agent CS curriculum auditor. Two-stage audit pipeline + parallel market-fit / topics analysis, all wired through a FastAPI web UI:

- **Auditor** — two-stage pipeline:
  - **Extractor** (Sonnet 4.6) surfaces every candidate with high recall, decides nothing.
  - **Adjudicator** (Opus 4.7) flags or skips each candidate with a primary-source citation, full burden-of-proof on SKIP.
- **Market-fit** (Opus 4.7) extracts the actual taught skillset, compares against role-tagged postings, returns gaps.
- **Topics** (Opus 4.7) ranks gaps into prescriptions ordered by market frequency.

All four agents run end-to-end through the orchestrator (`stale/web/runner.py`). Stage 1 (auditor + scope detection) fires on slide upload; stage 2 (market-fit + topics) fires on role submission. Both Market-fit and Topics now receive Auditor's verified findings so neither can recommend extending a flagged pattern.

A deterministic Python pass re-fetches every citation URL and re-anchors every `slide_ref` against the curriculum text — catches fabricated quotes and mis-attributed slide refs at zero LLM cost.

## What works (verified end-to-end)

| Component | Status | Notes |
|---|---|---|
| PDF + PPTX extraction with OCR fallback | ✅ | `tools/extract.py`; OCR'd text tagged so auditor doesn't cite it verbatim |
| 4 persistent Managed Agents | ✅ | Extractor, Adjudicator, Market-fit, Topics — IDs in `.env` |
| Two-stage auditor pipeline | ✅ | Resume-on-crash from `candidates.json`; auto-reconnect on SSE errors |
| Citation verifier (URL re-fetch + substring) | ✅ | Drops fabricated quotes |
| `slide_ref` verifier (re-anchor against curriculum) | ✅ | Catches "Lecture 01 vs 02" style mis-attribution |
| Scope detector | ✅ | `subject_area`, `pedagogical_level`, `course_intent`, `depth_bound`, `one_liner` |
| Strict-scope market-fit | ✅ | Only flags gaps the course already partially teaches |
| Auditor → Market-fit consistency | ✅ | Market-fit now receives Auditor findings (`runner.py:158-184`) |
| Auditor → Topics consistency | ✅ | Topics never extends a flagged pattern |
| FastAPI web UI (Findings · Market-fit · Topics tabs) | ✅ | `python -m stale.web` |
| Status pill respects per-stage failure | ✅ | `done.marker` with `market_fit_ok=false` → "error", not "done" |
| Test suite | ✅ | `pytest tests/` — 22 passed (incl. setup idempotency, kept-finding summary consistency, AI run market-fit/topic consistency, cross-tab AsyncTask regression, UI wording checks, repo-wide secret-shape/account-ID scans, claim-expansion checks, and retest-with-new-role pre-flight + endpoint behavior) |
| SSE reconnect/dedup on all 4 agents | ✅ | Shared via `stale/agents/_sse.py` |
| Index filters ghost / aborted runs | ✅ | `_list_runs` skips runs with no auditor output |
| Retest with new role (skip auditor) | ✅ | `POST /run/<id>/retest` clones audit data into a new run and runs only stage 2. Falls back to `<repo>/curriculum/<course>/` when the source run has no own curriculum. New run's meta records `_source_run`. |
| 11 curated demo runs ship without API key | ✅ | `output/runs/` — 0.78 MB total, no curriculum or transcripts |

## How to run

```bash
# 1. clone + install (editable — see "Installation note" below)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. configure
cp .env.example .env
# put ANTHROPIC_API_KEY=sk-ant-... in .env
# requires Anthropic API access with Managed Agents beta enabled

# 3. one-time agent setup (creates your 4 Managed Agents on Anthropic, writes IDs to .env)
python -m stale.setup_agents
# safe to rerun; use --force only to create fresh IDs

# 4. run the web UI
python -m stale.web
# open http://127.0.0.1:8000/

# 5. (optional) run tests
pytest
```

### Installation note

`pip install -e .` is the supported install mode. The web app's templates and static files ship via `[tool.setuptools.package-data]` so a wheel install also works for the UI; however, the runtime expects `data/job_postings.json` to be at repo root, so generating new runs on a wheel-only install requires also copying `data/` next to the working directory or pointing `DEFAULT_POSTINGS` elsewhere. For typical use, clone + editable install.

## Demo runs that ship in the repo

11 curated runs under `output/runs/`. Each pair `(course, role)` is unique:

Counts are **verified kept findings** (after deterministic citation re-fetch + slide_ref re-anchoring). Raw adjudicator output is in `findings.json`; the verified subset that the UI displays is in `findings_kept.json`.

| Run dir | Course | Role | Verified findings |
|---|---|---|---:|
| `refactor_oop1_20260505T215900Z` | Object Oriented Programming 1 | backend | 10 |
| `refactor_oop2_20260506T135257Z` | Object Oriented Programming 2 | backend | 8 |
| `web_20260428T073022Z` | Software Engineering | backend | 4 |
| `web_20260428T132556Z` | AI | ml_data | 1 |
| `web_20260428T195807Z` | Operating Systems | devops_sre | 3 |
| `web_20260428T202557Z` | Computer and Data Security | security | 4 |
| `web_20260430T104101Z` | Algorithm Analysis and Design | backend | 2 |
| `web_20260504T162848Z` | Mobile Application Development | mobile | 13 |
| `web_20260504T162928Z` | Software Engineering | fullstack | 3 |
| `web_20260504T210835Z` | Database Concepts and Design | ml_data | 2 |
| `web_20260504T212437Z` | Fundamentals of Communications and Networks | devops_sre | 5 |

Each run keeps `meta.json`, `scope.json`, `done.marker`, `stage1.marker`, and the structured agent outputs (`findings.json`, `findings_kept.json`, `verified.json`, `candidates.json`, `skipped.json`, `market_fit.json`, `topics.json`, `session_meta.json`). Shipped `session_meta.json` files keep non-sensitive run shape only; account-linked session, agent, environment, and file IDs are stripped. Curriculum PDFs, `events.jsonl`, and `agent_text.md` are stripped — see `.gitignore` for the whitelist.

## What's left (optional polish)

1. **Multimodal extraction** — Decision 10's deferred path. OCR catches ~80% of code screenshots; multimodal would close the gap.
2. **Persistent multi-course profile.** A student profile that aggregates findings across an entire degree, not just one course at a time. See [`memory/project_legacylifter_v2_ideas.md`](.) (parked).
3. **Live job-postings backend.** The `.invalid` TLD on every posting URL signals these are synthetic. A live HN "Who's Hiring" scraper would slot in behind the same Files API contract.
4. **Wheel-distributable data file.** Move `data/job_postings.json` inside `stale/` and load via `importlib.resources` so non-editable installs work end-to-end. Currently scoped to editable install only.

## Where everything lives

```
LegacyLifter/
├── README.md                            ← portfolio-facing entry point
├── STATE.md                             ← you are here
├── LICENSE                              ← MIT
├── pyproject.toml                       ← package-data declares prompts/, templates/, static/
├── .env.example                         ← API key + 5 agent/env IDs to fill in
├── .gitignore                           ← ignores output/ by default; whitelists 11 demo runs
│
├── docs/                                ← design record
│   ├── README.md
│   ├── architecture.md
│   ├── decisions.md                     ← 15 entries (most recent: retest with new role)
│   ├── build-plan.md
│   ├── budget.md
│   └── demo-plan.md
│
├── data/
│   ├── README.md                        ← dataset methodology
│   ├── build_postings.py                ← deterministic generator (SEED=42)
│   └── job_postings.json                ← 108 role-tagged postings
│
├── stale/
│   ├── setup_agents.py                  ← one-time agent creation
│   ├── tools/extract.py                 ← PDF/PPTX → text + OCR fallback
│   ├── upload.py                        ← Files API helpers
│   ├── verify.py                        ← deterministic citation verifier
│   ├── role_resolver.py                 ← free-text role → enum (Haiku)
│   ├── scope_detector.py                ← course → scope dict (Sonnet)
│   ├── prompts/
│   │   ├── __init__.py                  ← marks as package so package-data ships these
│   │   ├── extractor.md
│   │   ├── adjudicator.md
│   │   ├── auditor.md                   ← legacy (kept for reference)
│   │   ├── market_fit.md
│   │   └── topics.md
│   ├── agents/
│   │   ├── auditor.py                   ← extractor + adjudicator orchestration
│   │   ├── market_fit.py                ← now accepts auditor_findings
│   │   ├── topics.py                    ← consumes market-fit + auditor_findings
│   │   ├── _slide_ref_verifier.py       ← deterministic slide-attribution check
│   │   └── _json_extract.py
│   └── web/
│       ├── app.py                       ← FastAPI; status pill respects per-stage failure
│       ├── runner.py                    ← background pipeline
│       ├── __main__.py
│       ├── templates/                   ← base/index/run.html
│       └── static/                      ← style.css + run.js
│
├── tests/
│   ├── test_web_run_page.py             ← 12 tests (curated render, UI wording, retest, consistency checks)
│   └── test_no_secrets_shipped.py       ← 1 test (repo-wide secret-shape scan)
│
└── output/runs/                         ← 11 curated demo runs
```

## Key design calls

The full record is in [`docs/decisions.md`](docs/decisions.md). The load-bearing recent ones:

- **Decision 14** — Third pre-ship review fixes. Real Google API keys redacted out of Mobile findings (kept the `AIza` prefix for educational signal), `.env.example` inline-comment trap fixed, stale `.bak` deleted, repo-wide secrets scan added to CI.
- **Decision 13** — Second pre-ship review fixes. Mobile demo patched for cross-tab consistency, ghost-run filter on the index, shared SSE helper (`stale/agents/_sse.py`) for Market-fit + Topics, doc counts now show verified-kept findings.
- **Decision 12** — First pre-ship review fixes. Test fixture, status-pill semantics, Auditor → Market-fit wiring, package-data declarations.
- **Decision 11** — Two-stage extractor → adjudicator architecture. Extractor on Sonnet 4.6, Adjudicator on Opus 4.7. Verbatim-quote rule split into two fields (claim + citation_excerpt) to forbid stitching.
- **Decision 10** — Tesseract OCR fallback for image-based slide content.
- **Decision 9** — Generated job-postings dataset (SEED=42, `.invalid` TLD), not scraped.
- **Decision 8** — Deterministic citation verification, not a Verifier agent.
- **Decision 6** — `target_role` scopes the *output lens* only; input is always full curriculum.

## Hidden landmines (preserve these)

1. **Stream-first ordering.** Always open `client.beta.sessions.events.stream(session.id)` *before* sending the kickoff message. All four drivers do this; preserve the pattern.
2. **SSE auto-reconnect with replay dedup.** The auditor session can drop with `httpx.RemoteProtocolError`; the driver retries up to 6 times with backoff. `seen_event_keys` deduplicates replayed events. Don't remove either.
3. **Resume from `candidates.json`.** The two-stage auditor can resume from extractor output if the adjudicator crashes. Don't break this.
4. **Verifier substring tolerance is calibrated.** Paren-stripping on both sides + 6-word-or-2-phrase rule. Looser → fabricated quotes pass. Stricter → verified rate collapses.
5. **`slide_ref` verifier rewrites attribution.** Don't bypass it — it caught real Lecture 01 vs Lecture 02 misattribution in the OOP 1 run.
6. **Adjudicator severity discipline.** Pedagogical conventions like a hardcoded password in a teaching example are `low`, not `critical`. Don't relax.
7. **Strict scope rule for market-fit.** Never recommend a topic the course covers 0% of, regardless of market demand.
