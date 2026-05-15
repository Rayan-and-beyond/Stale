# Stale — Reference Datasets

Pre-fetched, version-controlled data the agents read at runtime.

## `job_postings.json`

108 role-tagged postings used by the **Market-fit** agent to compute "what % of postings for the target role demand this skill."

### Why generated, not scraped

Live scraping LinkedIn / Indeed is fragile (bot-blocking, terms-of-service issues) and not reproducible. A code-driven generator is the most defensible alternative — every demo claim like *"47% of backend postings demand Postgres"* traces back to a frequency table that is fully visible in [`build_postings.py`](build_postings.py). Any judge can read the role profiles and verify the distributions.

### Methodology

For each of 9 roles, [`build_postings.py`](build_postings.py) encodes:

1. A pool of skill keywords reflecting real 2026 demand
2. A per-skill frequency rate (probability the skill appears in a posting for that role)
3. A set of plausible title/seniority/stack variations

The generator then samples `POSTINGS_PER_ROLE = 12` postings per role independently, with senior/staff titles getting slightly higher density (×1.10) than junior (×0.92).

### Frequency calibration sources

The per-skill rates are calibrated against publicly available industry surveys (read at design time; not re-fetched at agent runtime):

- **Stack Overflow Developer Survey 2024** — language and tooling popularity
- **GitHub Octoverse 2025** — language usage trends
- **LinkedIn 2025 Emerging Jobs** — skill demand by role
- **HackerRank Developer Skills Report 2024**
- **JetBrains State of Developer Ecosystem 2024**

The numbers are not pulled from a CSV — they are encoded as informed estimates for the role profiles. See `ROLE_PROFILES` in `build_postings.py`.

### Roles covered

`backend`, `frontend`, `fullstack`, `ml_data`, `mobile`, `devops_sre`, `security`, `embedded`, `game`

### URL field is synthetic

Each posting has `url: https://stale-dataset.invalid/postings/post_NNN`. The `.invalid` TLD is reserved by RFC 2606 specifically to flag non-resolvable example URLs. Posting URLs do not point to real listings; this is by design — we never claim to have scraped real companies.

### Regenerating

```sh
python data/build_postings.py
```

Deterministic — uses `SEED = 42`, so re-running produces the same 108 postings.

### Schema

```json
{
  "metadata": {...},
  "postings": [
    {
      "id": "post_001",
      "role": "backend",
      "company": "Acme Cloud",
      "title": "Senior Backend Engineer",
      "url": "https://stale-dataset.invalid/postings/post_001",
      "skills_required": ["docker", "postgres", "python", "rest api", "sql"],
      "skills_preferred": ["kafka", "redis", "terraform"],
      "raw_text": "Acme Cloud is hiring a Senior Backend Engineer..."
    }
  ]
}
```
