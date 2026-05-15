import sys

from stale import setup_agents


def _clear_managed_agent_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for key in setup_agents.MANAGED_AGENT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_setup_agents_is_noop_when_ids_already_exist(tmp_path, monkeypatch, capsys):
    _clear_managed_agent_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STALE_ENVIRONMENT_ID=env_existing",
                "STALE_AUDITOR_AGENT_ID=agent_extractor",
                "STALE_ADJUDICATOR_AGENT_ID=agent_adjudicator",
                "STALE_MARKET_FIT_AGENT_ID=agent_market",
                "STALE_TOPICS_AGENT_ID=agent_topics",
                "",
            ]
        )
    )
    monkeypatch.setattr(setup_agents, "ENV_FILE", env_file)
    monkeypatch.setattr(sys, "argv", ["setup_agents"])

    assert setup_agents.main() == 0
    assert "nothing to create" in capsys.readouterr().out


def test_setup_agents_refuses_partial_existing_ids(tmp_path, monkeypatch, capsys):
    _clear_managed_agent_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "STALE_ENVIRONMENT_ID=env_existing",
                "STALE_AUDITOR_AGENT_ID=agent_extractor",
                "STALE_ADJUDICATOR_AGENT_ID=",
                "STALE_MARKET_FIT_AGENT_ID=",
                "STALE_TOPICS_AGENT_ID=",
                "",
            ]
        )
    )
    monkeypatch.setattr(setup_agents, "ENV_FILE", env_file)
    monkeypatch.setattr(sys, "argv", ["setup_agents"])

    assert setup_agents.main() == 1
    assert "Partial Managed Agent configuration" in capsys.readouterr().err
