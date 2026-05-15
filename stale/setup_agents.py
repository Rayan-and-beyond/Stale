"""One-time setup: create the environment and the 4 Managed Agents (Extractor +
Adjudicator for the audit pipeline, plus Market-fit and Topics), persist their
IDs into the local `.env` file.

Run this once per project. If all IDs already exist in `.env`, the command
exits without creating anything. Pass `--force` only when you deliberately want
to create a fresh environment and fresh agents.

Usage:
    python -m stale.setup_agents
    python -m stale.setup_agents --force

Reads ANTHROPIC_API_KEY from .env. Writes/updates these keys in .env:
    STALE_ENVIRONMENT_ID
    STALE_AUDITOR_AGENT_ID         (Extractor — Sonnet 4.6)
    STALE_ADJUDICATOR_AGENT_ID     (Adjudicator — Opus 4.7)
    STALE_MARKET_FIT_AGENT_ID
    STALE_TOPICS_AGENT_ID
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
ENV_FILE = REPO_ROOT / ".env"

MODEL = "claude-opus-4-7"
# The Extractor is a high-recall surfacing task, not a judgment task — Sonnet
# 4.6 handles it at ~1/5 the cost of Opus with no measurable quality drop.
# The Adjudicator stays on Opus because it does the hard flag/skip + citation
# reasoning and is the quality bottleneck.
EXTRACTOR_MODEL = "claude-sonnet-4-6"

AGENT_TOOLSET = {"type": "agent_toolset_20260401", "default_config": {"enabled": True}}
MANAGED_AGENT_ENV_KEYS = [
    "STALE_ENVIRONMENT_ID",
    "STALE_AUDITOR_AGENT_ID",
    "STALE_ADJUDICATOR_AGENT_ID",
    "STALE_MARKET_FIT_AGENT_ID",
    "STALE_TOPICS_AGENT_ID",
]


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def upsert_env_var(key: str, value: str) -> None:
    """Set or update KEY=value in .env. Creates the file if it doesn't exist."""
    lines: list[str] = []
    found = False
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def configured_ids() -> dict[str, str]:
    return {key: os.environ.get(key, "").strip() for key in MANAGED_AGENT_ENV_KEYS}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Stale's Anthropic Managed Agents once and persist IDs to .env."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Create a fresh environment and fresh agents even if IDs already exist.",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    load_dotenv(ENV_FILE)

    existing = configured_ids()
    present = [key for key, value in existing.items() if value]
    missing = [key for key, value in existing.items() if not value]
    if present and not args.force:
        if not missing:
            print("Managed Agents already configured in .env; nothing to create.")
            print("Use `python -m stale.setup_agents --force` to create fresh IDs.")
            return 0
        print(
            "ERROR: Partial Managed Agent configuration found in .env. "
            "Refusing to create a mixed setup.",
            file=sys.stderr,
        )
        print(f"Configured: {', '.join(present)}", file=sys.stderr)
        print(f"Missing: {', '.join(missing)}", file=sys.stderr)
        print(
            "Fill the missing IDs, clear the existing IDs, or rerun with --force "
            "to create a fresh environment and fresh agents.",
            file=sys.stderr,
        )
        return 1

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Copy .env.example → .env and fill it in.",
              file=sys.stderr)
        return 1

    client = anthropic.Anthropic(api_key=api_key)

    print("Creating environment...")
    env = client.beta.environments.create(
        name="stale-env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"  environment_id = {env.id}")
    upsert_env_var("STALE_ENVIRONMENT_ID", env.id)

    print("\nCreating Extractor agent (Phase 1 of audit pipeline)...")
    extractor = client.beta.agents.create(
        name="Stale Extractor",
        model=EXTRACTOR_MODEL,
        system=load_prompt("extractor"),
        tools=[AGENT_TOOLSET],
        description="Phase 1 of the audit pipeline. Surfaces every candidate "
                    "stale pattern in CS course materials with surrounding "
                    "context. High recall; does not decide flag/skip.",
    )
    print(f"  extractor_id = {extractor.id} (version {extractor.version})")
    # Historical name kept for backwards compatibility with existing .env files.
    upsert_env_var("STALE_AUDITOR_AGENT_ID", extractor.id)

    print("\nCreating Adjudicator agent (Phase 2 of audit pipeline)...")
    adjudicator = client.beta.agents.create(
        name="Stale Adjudicator",
        model=MODEL,
        system=load_prompt("adjudicator"),
        tools=[AGENT_TOOLSET],
        description="Phase 2 of the audit pipeline. Decides per-candidate "
                    "whether a pattern is taught as recommended (FLAG) or "
                    "shown as a counterexample (SKIP). Cites primary sources.",
    )
    print(f"  adjudicator_id = {adjudicator.id} (version {adjudicator.version})")
    upsert_env_var("STALE_ADJUDICATOR_AGENT_ID", adjudicator.id)

    print("\nCreating Market-fit agent...")
    market_fit = client.beta.agents.create(
        name="Stale Market-fit",
        model=MODEL,
        system=load_prompt("market_fit"),
        tools=[AGENT_TOOLSET],
        description="Compares the actual taught skillset of a CS curriculum against current job-market demand for a target role. Identifies quantitative gaps.",
    )
    print(f"  market_fit_id = {market_fit.id} (version {market_fit.version})")
    upsert_env_var("STALE_MARKET_FIT_AGENT_ID", market_fit.id)

    print("\nCreating Topics agent...")
    topics = client.beta.agents.create(
        name="Stale Topics",
        model=MODEL,
        system=load_prompt("topics"),
        tools=[AGENT_TOOLSET],
        description="Given a market-fit gap analysis, prescribes ranked missing topics with citations and course-mapping recommendations.",
    )
    print(f"  topics_id = {topics.id} (version {topics.version})")
    upsert_env_var("STALE_TOPICS_AGENT_ID", topics.id)

    print("\n✅ Setup complete. IDs persisted to .env")
    print(f"   {ENV_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
