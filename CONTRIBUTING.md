# Contributing

Every finding Stale reports carries a verbatim slide quote, a primary-source citation, and a re-fetched excerpt. Contributions are held to the same bar: no fabricated quotes, no dead links, no unattributed claims.

Before opening a pull request:

```bash
python -m pytest -q
```

Rules:

- Citation changes must re-fetch: every cited URL gets re-requested and substring-matched at zero LLM cost, same as the pipeline. A PR with a citation no deterministic pass can re-anchor will be asked to fix or drop it.
- New entries in the 7-bucket taxonomy need a slide-quote example, a category justification, and a "what to teach instead" replacement.
- Market-fit changes stay in strict scope: only gaps the course already partially covers. Never invent topics out of thin air.
- The secrets scan (`tests/test_no_secrets_shipped.py`) runs in CI. Real API keys, session IDs, or account-linked IDs in fixtures, runs, or docs will fail the build — use the `.env.example` shapes.
- Keep LLM-costly paths optional in tests. Unit tests use mocked responses and never call the real Managed Agents API.
