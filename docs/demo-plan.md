# Stale — Demo Plan

The submission requires a **3-minute recorded demo video**. Recorded — not live — which means we can edit cuts, speed up agent work, and curate inputs for maximum punch. The hackathon brief: "easier to demo than to explain."

## Principle: visual-first output design

Outputs must be self-evident on screen. Don't rely on voiceover to explain what's happening. Headline numbers, gap charts, and citation cards should each land at-a-glance.

## 3-minute arc

| Time | Beat | Visual |
|---|---|---|
| 0:00–0:15 | Cold open — pain hook | "I'm a senior CS student. 4 years. $X. Today I find out if any of it was real." Camera on real degree audit page or transcript. |
| 0:15–0:30 | Drop curriculum | Real `/curriculum` folder dragged onto Stale. File counter ticks: "8 courses, ~120 decks, 31 assignments." |
| 0:30–0:50 | Orchestrator forks 3 agents | Diagram lights up: Auditor, Market-fit, Topics. Visualized as a fan-out animation. |
| 0:50–1:30 | Auditor + Market-fit in parallel | Fast cuts: findings appearing with citations resolving. Real ones from your slides. Voiceover lists 2–3 punchiest. |
| 1:30–2:00 | Topics ranks gaps | "Top 10 missing skills, 89% job-posting overlap on distributed systems." Animated bar chart. |
| 2:00–2:40 | Headline reveal | Big card: "Your degree prepared you for the [year] job market." Below: count of audit findings, count of critical market gaps. |
| 2:40–3:00 | Outro / call to action | "Stale found 312 outdated items, 47 critical market gaps. Built in 24 hours on Anthropic Claude Code." |

## Curating the input

For the recording specifically, the demo doesn't have to be a faithful slice of one full run. We **curate**:

- Pick the 6–8 most outdated-looking decks across the user's curriculum.
- Pick the 3–4 most damning market gaps.
- Speed up everything else.
- The final report shown on screen is a real Stale output, but the on-screen findings are filtered/highlighted to the punchiest ones.

This is normal for hackathon demos — we're not faking, we're editing for clarity.

## Recording approach

1. **Pre-bake outputs.** Run Stale once with the curated curriculum. Save the agent outputs and report HTML to disk. Use them as the "live" content during recording.
2. **Screen capture + voiceover.** Record screen at 1080p. Voiceover after the fact, not during, so we can edit freely.
3. **Speed up agent work segments.** A real agent run takes minutes; the recording shows it as 30–60 seconds via 4×–8× speedup with key findings unscrubbed.
4. **Music.** Light, tense-then-resolving — don't overdo it.
5. **Captions.** On for accessibility and for when judges watch muted.

## What lands in 3 minutes (per judging weight)

- **Impact (30%):** "Your degree prepared you for the wrong decade" is the universal CS-student pain in one line.
- **Demo (25%):** Visual-first, citation-backed, role-personal output.
- **Opus 4.7 use (25%):** Multi-agent orchestration + 1M context for full curriculum + adaptive thinking + agentic verification chains. Demo briefly shows the agent diagram.
- **Depth & Execution (20%):** Three agents shipping clean, structured output. Show the actual report with real findings.

## What we don't put in the video

- Architecture diagrams beyond the 5-second fan-out shot
- Code
- Long voiceover explanation of how it works
- Fake/staged findings — every shown citation must actually link

## Side prizes to angle for

- **Best Managed Agents ($5K):** Yes — multi-agent orchestration is core to the architecture. Mention it briefly in voiceover.
- **Most Creative Opus 4.7 Exploration ($5K):** Possible — adaptive thinking + 1M context + multi-agent is a defensible "creative use" claim.
- **Keep Thinking ($5K):** Less aligned but worth a mention if there's room.

## Submission deliverables checklist

- [ ] Public GitHub repo (open source)
- [ ] 3-min demo video (uploaded, linked from README)
- [ ] 100–200 word summary in README
- [ ] Run instructions in README
- [ ] License file
- [ ] Pre-fetched data files committed to repo
