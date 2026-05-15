# Stale — Project Docs

Durable design record for the Stale curriculum auditor. These docs are version-controlled and **are the source of truth** for project decisions — do not rely on chat history alone, since context bloats and key decisions get lost across conversations.

## Files

- [architecture.md](architecture.md) — system design, agent contracts, data flow, repo layout
- [decisions.md](decisions.md) — running decision log: what we considered, what we picked, why
- [build-plan.md](build-plan.md) — phased build sequence with deliverables and cut order
- [budget.md](budget.md) — API spend plan ($90 target, $150 cap) + discipline rules
- [demo-plan.md](demo-plan.md) — 3-minute recorded demo arc + curation approach

## How to use these docs

- **Before starting work each session,** skim `architecture.md` and `decisions.md` to reload context.
- **When a design choice is made,** update the relevant doc *immediately* — don't wait for end-of-session summarization.
- **When changing a previous decision,** add a new entry in `decisions.md` (don't edit history).

## Project state (paused 2026-04-27)

> **Hackathon ended; project paused before submission.** Full hand-off + resume notes in [`STATE.md`](../STATE.md) at repo root.

- **All 3 agents work end-to-end on a single course.** Smoke runs:
  - Auditor on Computer & Data Security → 10 findings, 10/10 verified against NIST/Oracle primary sources (~$0.30, 6.8 min)
  - Market-fit on same course (`role=security`) → 24 gaps, sharp headline (~$0.40, 6.5 min)
  - Topics on Market-fit's output → 10 ranked prescriptions (~$0.15, 2.5 min)
- **Total spend so far:** ~$0.85 of the $90 budget.
- **Web UI renders Auditor results** (Findings tab with verified citation cards). Market-fit + Topics tabs not built yet.
- **Orchestrator not started** — running the 3 agents currently requires three separate CLI invocations.
- **Curriculum on disk:** 8 courses.
- **Action items if revived:** see [`STATE.md`](../STATE.md).
