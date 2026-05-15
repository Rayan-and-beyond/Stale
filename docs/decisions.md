# Stale — Decision Log

A running record of design choices. Each entry: what we considered, what we picked, and why. New entries go on top.

---

## Decision 15 — Retest with new role (skip auditor) (2026-05-07)

**Feature:** From any completed run page, the user can submit a new target role and spawn a fresh run that reuses the existing audit data — `findings.json`, `findings_kept.json`, `verified.json`, `scope.json`, `stage1.marker`. Only stage 2 (Market-fit + Topics) re-runs against the new role. The expensive auditor stage (Sonnet 4.6 extractor + Opus 4.7 adjudicator) is skipped entirely.

Why it matters: switching roles to compare gaps (e.g. Networks → security vs Networks → devops_sre) used to require a full re-run including the ~$30 audit. The audit is role-agnostic — it surfaces dead/deprecated/wrong content regardless of who's reading the slides. Forcing it to re-run was waste. Validated cost: a Security course retest with a new role takes ~14 min wall-clock and ~$8 (Market-fit + Topics only), vs ~30 min and ~$38 for a full re-run.

**Implementation** (`stale/web/app.py`):

- `POST /run/{run_id}/retest` — accepts `role_text`, resolves via `role_resolver`, creates new run dir, copies the small auditor JSONs (~50KB), symlinks the curriculum, copies `scope.json` + `stage1.marker`, spawns `run_stage2` in a thread, redirects to the new run page.
- New run's `meta._source_run` records the source run id. Run page renders a "↩ derived from <source>" badge linking back.
- Pre-flight check `_can_retest_with_new_role(run_dir)`: True iff audit completed AND a curriculum source is reachable. Curriculum source resolution tries `run_dir/curriculum/` first, then falls back to `<repo>/curriculum/<course_dir_name>/` (case- and whitespace-tolerant) — so the user's local curriculum tree covers shipped curated demos that don't ship their own slides.
- Retest form is a `<details>` collapsed by default on the run page, just below the resolved-role card. Hidden entirely when not retestable, so portfolio viewers cloning the repo without curriculum see no broken affordance.

**What didn't make the cut:**

- Sibling-runs panel ("see other roles tested on this course") — value is real but mechanism is fiddly (need to scan all runs for matching `_source_run` chains). Defer.
- Inline retest as a tab inside the source run rather than a new run dir. Rejected: separation makes diffing easy, lineage is explicit, no schema migration needed.
- Fuzzy course-name matching was added after the strict matcher (strip+lowercase) left 2 of 11 demos non-retestable due to curriculum-folder drift ("&" vs "and", "PDF" suffix). The matcher in `_find_curriculum_course_dir` now scores token overlap on top of normalized-key compare, with a 0.5 minimum and a containment bonus. All 11 curated demos resolve to a local curriculum dir; tests assert universal retestability (`test_retest_button_appears_for_all_completed_demo_runs`). False-positive risk is bounded by the 0.5 score floor + the fact that the curriculum dir tree is small and curated.

---

## Decision 14 — Third pre-ship review: secrets, env-file hygiene, backup file (2026-05-06)

A third reviewer pass found three remaining release blockers, none of them related to analysis quality but all real ship-stoppers. All accepted.

**Real Google API keys shipped in the curated Mobile run.** The Mobile slide deck contained two hardcoded `AIzaSyD-…` Maps API keys as a teaching example. The Auditor correctly quoted them verbatim in `claim` and `citation_excerpt` fields (the verbatim-quote rule is what makes findings grep-checkable against the source PDF) — but those quotes then went into `findings.json`, `findings_kept.json`, and `verified.json`, all of which ship with the curated demo set. So a fresh clone of the repo had two real-looking 39-character Google keys in plaintext on disk.

Considered three options:

1. *Drop the finding entirely.* Rejected — the hardcoded-API-key finding is one of the strongest demo signals and explicitly the kind of thing the Auditor exists to catch. Cutting it would be self-defeating.
2. *Rewrite the keys to look obviously synthetic (`AIzaSyAAAAAAA…`).* Rejected — a synthetic-looking placeholder weakens the educational point that "this is what a real Maps key looks like in code." Reviewers wouldn't be able to tell from the artifact whether the underlying detection had ever worked on real data.
3. *Redact in place, preserving the `AIza` prefix.* Picked. Replaced both keys with `AIza<REDACTED>` across all three JSON files. The finding still reads correctly: "Replace YOUR_API_KEY with You API KEY: AIza\<REDACTED\> -OR- AIza\<REDACTED\>" — the educational signal (this is a Google API key with the canonical prefix) survives, the secret value does not.

Added `tests/test_no_secrets_shipped.py` — a repo-wide regression scan over `stale/`, `data/`, `docs/`, `tests/`, `output/runs/`, `README.md`, `STATE.md`, `LICENSE`, `pyproject.toml`, `.gitignore`, and `.env.example`. Patterns: Google (`AIza[0-9A-Za-z_-]{20,}`), Anthropic (`sk-ant-[a-zA-Z0-9_-]{20,}`), AWS (`AKIA[0-9A-Z]{16}`). The `.env.example` file is on a small allowlist because it intentionally contains the `sk-ant-...` placeholder shape. Test fails CI loudly if any future commit re-introduces a real-looking key anywhere in scanned scope.

**`.env.example` had inline comments after `=`.** `python-dotenv` parses everything after the `=` up to the newline as the literal value: trailing whitespace, the `#`, and the comment text all go in. So `STALE_AUDITOR_AGENT_ID=          # Phase 1...` leaves `os.environ.get("STALE_AUDITOR_AGENT_ID")` returning a 50-character string starting with whitespace, which evaluates as truthy and silently breaks the "ID not set" guard in every agent driver. Moved every comment onto its own line above the variable. No code change required because no one ever ran `setup_agents` against the unfilled template; this was a latent ship-blocker waiting to bite the first cloner.

**Stale `.bak` artifact in the Mobile run.** When patching the Mobile market-fit JSON for Decision 13, an editor left `topics/topics.json.preauditorwiring.bak` next to the live file. The backup contained the pre-wiring "extend AsyncTask" guidance — exactly the contradiction Decision 13 was supposed to fix. Deleted. Added `*.bak` and `*preauditorwiring*` to `.gitignore` to defend against the same pattern recurring.

**Minor polish bundled with the same commit:**
- `/favicon.ico` route returns 204 (was 404, cluttering the dev console).
- `setup_agents.py` docstring corrected: said "3 Managed Agents" but Decision 11 split the auditor into Extractor + Adjudicator, so it's been creating 4 since 2026-05-05.
- Doc count corrections across `STATE.md` and `README.md` (test count was 2, now 4; decisions log was 12, now 14).

Test suite: **4 passed.** `grep -rE 'AIza[0-9A-Za-z_-]{20,}' output/runs/` clean.

Implementation: `output/runs/web_20260504T162848Z/auditor/{findings,findings_kept,verified}.json` (in-place redaction), `output/runs/web_20260504T162848Z/topics/topics.json.preauditorwiring.bak` (deleted), `tests/test_no_secrets_shipped.py` (new), `.env.example` (rewrite), `.gitignore` (`*.bak`, `*preauditorwiring*`), `stale/web/app.py` (favicon 204), `stale/setup_agents.py` (docstring), `STATE.md` + `README.md` (counts).

---

## Decision 13 — Second pre-ship review fixes (2026-05-06)

A second reviewer pass on the post-Decision-12 build flagged four follow-on items. All accepted; resolutions below. The reviewer specifically suggested adding a regression test to lock the Auditor → Market-fit consistency in place, which we did.

**Stale demo artifact undermines the new wiring.** Decision 12 wired Auditor findings into Market-fit, but the Mobile run's `market_fit.json` was generated *before* that wiring shipped, so `gaps[0].rationale` still said "executed inside the existing AsyncTask/Handler scaffolding" — exactly the contradiction the wiring was supposed to prevent. Two options: regenerate the run (real API spend on a course we already validated) or surgically patch the JSON to match the new framing. Picked the patch: rewrote the rationale to describe the lab as "executed on a java.util.concurrent.Executor with results delivered back to the UI thread via Handler(Looper.getMainLooper())" and added a sentence calling out that the existing AsyncTask scaffolding "is deprecated as of API 30 and should be replaced — not extended — when adding this REST lab." Net result: the demo now reads exactly the way a fresh run with the new wiring would.

**Index showed non-curated legacy runs.** `_list_runs()` iterated both `output/runs/` (curated) and `output/auditor/` (legacy single-stage dev runs). Three pre-curation directories (`output/auditor/`, `output/market_fit/`, `output/topics/`) lingered locally and rendered as ghosts on the index. Considered (a) removing legacy support entirely — too invasive; many code paths still branch on `_is_legacy_run`. Picked (b): delete the local legacy dirs *and* add a defensive filter in `_list_runs()` that drops any directory which has no `findings_kept.json`, no `findings.json`, and is not actively in-progress. That way future stale empty shells (failed early uploads, aborted runs) don't render as broken links either.

**Market-fit and Topics SSE loops were single-pass.** The Auditor pipeline (Decision 11) added `httpx.RemoteProtocolError` reconnect, replay-event dedup, and transient-`session.error` handling. Market-fit and Topics had none of that — a single network blip on a 90-second session would crash and lose the whole run. Considered: copy-paste the loop into both drivers (DRY violation, three places to maintain), or extract to a shared helper. Picked the extraction: `stale/agents/_sse.py` exposes `consume_session_stream(client, session_id, *, label, events_path, text_path, on_stream_open=...)`. The `on_stream_open` callback preserves the stream-first ordering invariant — caller passes a closure that sends the kickoff message; the helper invokes it after opening the initial stream and never on reconnect (the kickoff is already in the session's history; replay handles it). Auditor still has its own loop because it has session-creation specifics (resume from candidates.json, skip phase 1 if cached) that don't generalize cleanly; that asymmetry is documented.

**Doc counts mixed raw and verified findings.** STATE.md and README listed Adjudicator's raw FLAG count (e.g. OOP 1 = 20). The UI shows `findings_kept.json` — the subset that survives deterministic citation re-fetch + `slide_ref` re-anchoring (OOP 1 = 10). A reviewer reasonably treated this as the system over-claiming. Fixed: STATE.md's per-run table now shows verified counts and labels them as such; README's Auditor pipeline table now shows both columns ("Adjudicator FLAGs" + "Findings (verified)") so the verifier's pruning effect is visible, not hidden.

**Regression test (per reviewer's suggestion).** Added `test_mobile_market_fit_does_not_extend_asynctask` which scans every gap's rationale for "inside AsyncTask," "extending AsyncTask," "build on AsyncTask," and similar phrases. Locks the cross-tab consistency in place so a future regression on the Auditor → Market-fit wiring (or a stale demo file) fails CI loudly instead of silently re-introducing the contradiction. Test suite now: **3 passed**.

Implementation: `output/runs/web_20260504T162848Z/market_fit/market_fit.json` (patched), `stale/web/app.py:_list_runs` (filter), `stale/agents/_sse.py` (new), `stale/agents/market_fit.py` + `stale/agents/topics.py` (consume the helper), `tests/test_web_run_page.py` (new test), `STATE.md` + `README.md` (count corrections).

---

## Decision 12 — Pre-ship hygiene fixes (2026-05-06)

External reviewer flagged four issues against the curated portfolio build. All accepted; resolutions below.

**P1 — Test fixture pointed at a stale run id.** Test `test_oop_awaiting_role_run_renders` referenced `refactor_oop1_20260505T193638Z`, but that run was deleted during demo curation; the curated equivalent is `refactor_oop1_20260505T215900Z`. Renamed the test to `test_curated_run_renders` and updated the assertion (`role: backend` instead of "What role are you targeting?", since the curated run already has a role assigned). Both tests now pass.

**P1 — `done.marker` lied about status.** `run_stage2` always wrote `done.marker` in its `try` block, including when Market-fit or Topics's `_safe_call` swallowed an exception. The marker recorded `market_fit_ok` / `topics_ok` flags but `_run_status` only checked existence, so a partially-failed run rendered as ✅ done. Fixed at the status layer (`stale/web/app.py:_run_status`): if either flag is `False`, status is `error`. Status pill on the index and run pages now matches the actual outcome. Backwards-compatible — empty markers still render as done (legacy runs).

**P1 — Market-fit could contradict the Auditor.** Topics already received `auditor_findings` (Decision 11 set this up), but Market-fit did not. The Mobile run was the proof: Market-fit recommended extending REST work *inside* an AsyncTask scaffold while the Auditor flagged AsyncTask as deprecated; Topics later repaired the contradiction, but the Market-fit tab still showed the bad guidance. Threaded `auditor_findings` through `runner.run_stage2` → `run_market_fit` → kickoff message. Market-fit prompt now carries the same "do not extend a flagged pattern; replace it" block as Topics, with the AsyncTask example called out explicitly. Cross-tab consistency is now a single load-bearing block, not two divergent ones.

**P2 — Non-editable installs missed runtime assets.** `pyproject.toml` only declared `[tool.setuptools.packages.find]` for `stale*`. Jinja templates, static CSS/JS, and the prompt markdown live inside `stale/` but are non-`.py` so `find` skipped them. Added `[tool.setuptools.package-data]` for `stale.prompts/*.md` and `stale.web/templates/*.html` + `static/*`, plus `stale/prompts/__init__.py` so the directory is a real package. `setup_agents.py` was also fixed to resolve `PROMPTS_DIR` from `Path(__file__).parent / "prompts"` instead of the repo-root concatenation, so it works under wheel installs too. The remaining gap is `data/job_postings.json` at repo root; that's a runtime data file and stays editable-install-scoped (documented in STATE.md).

**Why fix all four before shipping:** Three are ship-blockers for credibility (red tests, false-success status, descriptively-wrong tab). The fourth is package hygiene that any reviewer doing `pip install .` (no `-e`) would hit immediately. None touch the analysis quality, which the same reviewer rated shippable.

Implementation: `stale/web/app.py:121-135`, `stale/web/runner.py:158-184`, `stale/agents/market_fit.py:54-148, 184-194, 290-298`, `pyproject.toml:29-38`, `tests/test_web_run_page.py`, `stale/prompts/__init__.py` (new), `stale/setup_agents.py:28-30`.

---

## Decision 11 — Two-stage extractor → adjudicator pipeline (2026-05-05)

The original auditor was one Opus session that decided FLAG vs SKIP inline as it read the curriculum. Result on the OOP 1 baseline: high precision, terrible recall. The agent silently rationalized away real deprecations with phrases like "this is probably a counterexample," and there was no way to inspect the candidates it had silently dropped.

Considered:

1. Tighter prompting on the single-stage auditor (more "do not skip without explicit reasoning" rules). *Rejected* — already tried twice; prompt engineering against silent drops doesn't work because the model never reaches the FLAG/SKIP decision visibly.
2. Two-stage pipeline: an Extractor that surfaces every candidate with high recall and decides nothing, then an Adjudicator that reads each candidate with full course context and explicitly FLAGs or SKIPs with burden-of-proof on SKIP. *Picked.*
3. Three-stage with a separate Verifier agent. *Rejected* — Decision 8 already chose deterministic Python verification.

**Why two-stage wins:** The Extractor's job is recall, not precision; that's a Sonnet 4.6 task at ~1/5 the Opus cost. The Adjudicator's job is judgment under full context with citation discipline; that stays on Opus 4.7 because it's the quality bottleneck. Splitting them makes the SKIP set inspectable (`skipped.json` with reasons), forces every FLAG to carry a verbatim citation_excerpt and a re-fetched URL match, and lets the Adjudicator be re-run from `candidates.json` if it crashes — saving the expensive Extractor pass.

**Side-effects worth keeping:**
- Verbatim-quote rule split into TWO fields (`claim` + `citation_excerpt`) that must each be byte-grep-able. Forbids stitching with "...".
- 7-bucket category taxonomy: `dead`, `renamed`, `doesnt_compile`, `runtime_wrong`, `deprecated`, `outdated_teaching`, `conceptual`. The `outdated_teaching` bucket was added when the OOP 2 reviewer pointed out we were conflating "API removed" with "this is just legacy code style."
- Severity discipline for security-style findings: pedagogical conventions (hardcoded password in a teaching example, `MD5("hello")` to demonstrate hashing) are `low`, not `critical`. Production-impact framing is reserved for production-impact claims.
- `auditor_findings` flow into Topics (and as of Decision 12, Market-fit) so neither downstream agent can recommend extending a flagged pattern.

**Cost on real runs:** Extractor 9.6M cached tokens ≈ $5; Adjudicator 8.9M cached tokens ≈ $25. Total per course ~$30, dominated by the Adjudicator's judgment work, which is exactly where we want the cost to land.

Implementation: `stale/agents/auditor.py` (orchestration, SSE auto-reconnect, resume-from-candidates), `stale/prompts/extractor.md`, `stale/prompts/adjudicator.md`, `stale/agents/_slide_ref_verifier.py` (deterministic slide-attribution re-anchoring).

---

## Decision 10 — Tesseract OCR fallback for image-based slide content (2026-05-04)

CS instructor decks routinely paste code as syntax-highlighted screenshots and ship architecture diagrams as raster images. The text-only extractor (`page.get_text()` for PDF, `shape.text_frame.text` for PPTX) was blind to all of it — a slide of "here's a Python example" rendered as a screenshot looked empty to the auditor.

Considered:

1. Leave it — note the limitation in the README. *Rejected.* Real slides we already audited (Mobile L11) had AsyncTask code samples as images that the auditor only knew about by name, not by content.
2. Multimodal pipeline — render every slide to PNG and pass image content blocks to the auditor agent alongside text. *Deferred.* Best fidelity but a real refactor: the auditor currently reads `.txt` files via Files API, would need a per-slide image-attach scheme + token-cost analysis.
3. Tesseract OCR fallback in the extractor. *Picked.* PPTX picture shapes get OCR'd directly; PDF pages with embedded images get rendered at 200dpi and OCR'd. Output is tagged `[OCR from picture]` / `[OCR from page image]` so the auditor knows it's noisy.

**Why the OCR path wins for v1:** No architectural change. ~5s per pptx, ~15s per heavy PDF on this hardware. Caught real code (`extends AsyncTask<String, Integer, String>`) on the first smoke test with character-level noise but recognizable substance.

**Trade-off accepted:** OCR mangles characters (`l`/`1`, `0`/`O`, dropped indentation). The auditor's system prompt was updated to (a) not flag findings purely on OCR'd code "looking wrong" and (b) never use OCR'd text as the verbatim citation excerpt — that would fail the deterministic verifier anyway.

**Future upgrade path:** Decision 2 (multimodal) becomes attractive once we have throughput data on OCR's miss rate. Implementation: `stale/tools/extract.py`.

---

## Decision 9 — Generated job-postings dataset, not scraped (2026-04-26)

Market-fit needs ~100 role-tagged postings to compare the curriculum against. Considered:

1. Live-scrape LinkedIn / Indeed each run — *Rejected.* Bot-blocking, ToS issues, not reproducible at demo time.
2. Hand-curate 30–50 real postings — *Rejected.* Fragile (URLs decay), labor-intensive, awkward to update.
3. Generate the dataset programmatically with role-specific skill frequency tables calibrated to public industry surveys — *Picked.*

**Why generation wins:** Every demo claim ("47% of backend postings demand Postgres") traces back to a frequency table that is fully visible in code ([`data/build_postings.py`](../data/build_postings.py)). A judge can read the role profiles and verify the distributions match what the 2026 market actually demands. Reproducible (`SEED = 42`).

**Honesty:** URLs use the `.invalid` TLD (RFC 2606) so it's clear we never claim to have scraped real listings. Skill frequency rates are calibrated against Stack Overflow Developer Survey 2024, GitHub Octoverse 2025, LinkedIn 2025 hiring trends. Documented in [`data/README.md`](../data/README.md).

**Trade-off accepted:** A judge could push back with "but those aren't real postings." The defense is the methodology — the *frequencies* are calibrated, the *companies* are placeholders. If we revive this project, easiest upgrade is to swap the generator for a live HN "Who's Hiring" scrape with the same skill-extraction stage; the agents downstream don't change.

---

## Decision 8 — Deterministic citation verification, not a Verifier agent (2026-04-26)

User raised: how do we know the Auditor's "updated" claims are actually valid? Considered:

1. Trust the Auditor's prompt-level self-verification. *Rejected* — models can fabricate URLs and quotes that look plausible.
2. Add a second LLM-based Verifier agent. *Deferred* — doubles per-run spend on the auditor leg and adds a moving piece to the demo.
3. Deterministic Python pass: re-fetch every `citation_url`, substring-check `citation_excerpt` against the page (HTML or PDF). *Picked.*

**Why deterministic wins for now:** It catches the most common LLM failure mode (made-up URLs, fabricated quotes) for ~zero cost. A substring check is more reliable than another agent's "yes this looks right" judgment for the existence question. It doesn't help with "is the suggested_replacement actually best practice?" — that's a real gap, but a smaller one we'll address only if the smoke-run findings look squishy.

**Demo angle:** "every claim verified against a primary source we re-fetched ourselves" is a stronger and more defensible line than "two agents argue."

Implementation: `stale/verify.py`. Runs between Auditor and report rendering. Outputs `verified.json` (annotated) + `findings_kept.json` (only verified findings render to the user).

---

## Decision 7 — PPTX preferred over PDF (2026-04-26)

PPTX exposes structured XML and **speaker notes**, often where the real substance lives. PDF parsing is layout-guessing and strips notes. Support both via `python-pptx` and PyMuPDF, but recommend the user upload PPTX whenever they have the choice.

`.ppt` (legacy binary format) not supported in v1 — would require LibreOffice conversion. If the user has any, we either convert manually or skip them.

---

## Decision 6 — Role parameter scopes output, not input (2026-04-26)

The system always ingests the **entire** CS curriculum. The `target_role` parameter only filters which job postings the market-fit agent compares against, and which gaps the topics agent ranks. Auditor is role-agnostic.

**Why:** The product pitch is "drop your full degree, find out what it taught you." Filtering input courses by role would compromise that. The role lens belongs on the output side, not the input side.

---

## Decision 5 — Cut continuity-mapper / project-spine (2026-04-26)

Considered as a fourth agent (find concrete artifact-reuse links between courses). Cut because:

- Insights are shallow — most students figure out "you can use the DB from term 1 in the Java app in term 2" themselves.
- Claims are unfalsifiable — "you *could* connect A to B" is a suggestion, not a finding. No primary source to cite.
- Doesn't address the user's stated pain (which is about outdated content + market preparedness, not silo-bridging).
- Demo moment is decoration, not punch.
- Building it eats hours we don't have.

3 tight agents (Audit + Market-fit + Topics) outscore 4 with one tagalong. Weighted score moved from ~7.85 → ~8.50 by cutting it.

---

## Decision 4 — Reject "archetype" constraint on project plans (2026-04-26)

Briefly considered constraining project-spine to pick from 4 fixed archetypes (chat platform, search engine, multiplayer game, ML platform). User pushed back: too deterministic, forces fake connections between courses that legitimately don't share one.

This pushback contributed to cutting project-spine entirely (Decision 5).

---

## Decision 3 — Add `target_role` parameter (2026-04-26)

Initially planned without role input. Added because:

- Sharper claims: "I want to be a backend engineer. 89% of backend postings demand Postgres. Your DB course teaches SQL*Plus only."
- Cleaner agent reasoning: market-fit no longer has to reconcile contradicting role demands; topics has a coherent universe to rank within.
- Better demo: specific and personal.
- Cost: ~1 hour of implementation. Job postings dataset already needed role tags.

Canonical role list: `backend`, `frontend`, `fullstack`, `ml_data`, `mobile`, `devops_sre`, `security`, `embedded`, `game`.

---

## Decision 2 — Per-feature agents, not per-course (2026-04-26)

Considered spinning up one agent per course (~30 sessions). Rejected because:

- Cost: 8–10× more sessions/containers for no benefit.
- Each per-course agent would internally need audit + market-fit + topics logic anyway = duplication.
- Per-feature agents already have the right shape — each is a specialized lens that processes all courses through one analytical pattern.

Per-course iteration still happens — it's `for course in courses:` *inside* each feature agent's session.

---

## Decision 1 — Lock to 3 agents (2026-04-26)

Audit, Market-fit, Topics. Auditor and Market-fit run in parallel. Topics runs sequentially after Market-fit (consumes its gap output).

Both Auditor and Market-fit ingest the full curriculum. Topics only ingests Market-fit's structured output. Project-spine cut (Decision 5).
