"""Behavior tests for per-platform gateway turn limits."""

import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.turn_limits import (
    resolve_gateway_turn_limits,
    wall_timeout_expired,
)


def test_platform_limits_tighten_only_the_selected_surface() -> None:
    config = {
        "agent": {
            "platform_limits": {
                "discord": {
                    "max_turns": 12,
                    "wall_timeout": 180,
                }
            }
        }
    }

    discord = resolve_gateway_turn_limits(
        config,
        "discord",
        default_max_iterations=90,
    )
    telegram = resolve_gateway_turn_limits(
        config,
        "telegram",
        default_max_iterations=90,
    )

    assert discord.max_iterations == 12
    assert discord.wall_timeout_seconds == 180
    assert telegram.max_iterations == 90
    assert telegram.wall_timeout_seconds is None


def test_platform_limit_cannot_enlarge_global_iteration_budget() -> None:
    limits = resolve_gateway_turn_limits(
        {
            "agent": {
                "platform_limits": {
                    "discord": {
                        "max_turns": 200,
                    }
                }
            }
        },
        "discord",
        default_max_iterations=40,
    )

    assert limits.max_iterations == 40


def test_invalid_values_fail_open_and_zero_disables_wall_timeout() -> None:
    limits = resolve_gateway_turn_limits(
        {
            "agent": {
                "platform_limits": {
                    "discord": {
                        "max_turns": "not-a-number",
                        "wall_timeout": 0,
                    }
                }
            }
        },
        "discord",
        default_max_iterations=90,
    )

    assert limits.max_iterations == 90
    assert limits.wall_timeout_seconds is None


def test_wall_timeout_uses_elapsed_monotonic_time() -> None:
    assert not wall_timeout_expired(
        100.0,
        180.0,
        now_monotonic=279.999,
    )
    assert wall_timeout_expired(
        100.0,
        180.0,
        now_monotonic=280.0,
    )
    assert not wall_timeout_expired(
        100.0,
        None,
        now_monotonic=10_000.0,
    )


@pytest.mark.asyncio
async def test_gateway_passes_discord_iteration_cap_to_agent(
    monkeypatch,
    tmp_path,
) -> None:
    """Exercise config propagation through the real gateway run path."""

    # The required repository runner intentionally launches Python with a
    # minimal environment. Give gateway.run an isolated home before import so
    # its module-level Path.home() use remains deterministic on Windows.
    local_appdata = tmp_path / "AppData" / "Local"
    roaming_appdata = tmp_path / "AppData" / "Roaming"
    local_appdata.mkdir(parents=True)
    roaming_appdata.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("APPDATA", str(roaming_appdata))

    import gateway.run as gateway_run
    from gateway.config import Platform
    from gateway.session import SessionSource

    class CapturingAgent:
        last_init = None

        def __init__(self, *args, **kwargs):
            type(self).last_init = dict(kwargs)
            self.tools = []

        def run_conversation(
            self,
            user_message,
            conversation_history=None,
            task_id=None,
            persist_user_message=None,
        ):
            return {
                "final_response": "ok",
                "messages": [],
                "api_calls": 1,
                "completed": True,
            }

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._service_tier = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(streaming=None)
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda source: SimpleNamespace(
            session_id="session-limited"
        ),
        load_transcript=lambda session_id: [],
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._enrich_message_with_vision = AsyncMock(return_value="ENRICHED")

    config = {
        "agent": {
            "platform_limits": {
                "discord": {
                    "max_turns": 12,
                    "wall_timeout": 180,
                }
            }
        }
    }
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: config)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_gateway_model",
        lambda config=None: "gpt-5.4",
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "***",
        },
    )

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(
        tools_config,
        "_get_platform_tools",
        lambda user_config, platform_key: {"core"},
    )

    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
    )
    result = await runner._run_agent(
        message="hi",
        context_prompt="",
        history=[],
        source=source,
        session_id="session-limited",
        session_key="agent:main:discord:dm:12345",
    )

    assert result["final_response"] == "ok"
    assert CapturingAgent.last_init["max_iterations"] == 12
