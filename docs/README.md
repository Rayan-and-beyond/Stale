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

## Project state

See [`STATE.md`](../STATE.md) at the repo root for the current truth. These docs in `docs/` are the durable design record (architecture, decisions, plan, budget, demo arc); `STATE.md` is the live hand-off doc.
