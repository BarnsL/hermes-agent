"""Regression tests for CRITICAL #25 (2026-07-18): user-selected model must stick.

Incident: the user selects an Anthropic model (Claude subscription OAuth) in
the desktop picker; a transient provider error (HTTP 400 "You're out of extra
usage", DeepSeek 402) activates the ``fallback_providers`` chain and the chat
silently lands on local Ollama qwen — which then 400s itself because the
global ``reasoning_effort: high`` rode along ("does not support thinking").
Three code defects made one quota blip a lasting downgrade:

1. ``GatewayRunner._sync_session_model_from_agent`` persisted the FALLBACK
   model into the session row's ``model`` column (observed: a Discord session
   kept ``qwen2.5:14b-instruct-q4_K_M`` for 5 days after one DeepSeek 402).
2. ``credential_pool._exhausted_ttl`` froze the only Anthropic credential for
   1 hour on a subscription-quota 400, even though the rolling plan window
   restores quota within minutes (verified live: 400 at 19:27, API 200 by
   19:39) — so turns skipped Anthropic long after quota returned.
3. The fallback reasoning re-resolution carried thinking params onto models
   that cannot think (covered config-side by ``agent.reasoning_overrides``
   and code-side by the capability gate in ``try_activate_fallback``).

These tests pin each layer. If a hermes update reverts any of them, this file
fails loudly.
"""

from __future__ import annotations

import json
from types import SimpleNamespace


class _FakeSessionDB:
    """Captures update_session_meta calls; mimics hermes_state COALESCE contract."""

    def __init__(self, row):
        self._row = row
        self.meta_calls = []

    def get_session(self, session_id):
        return dict(self._row) if self._row else None

    def update_session_meta(self, session_id, model_config_json, model=None):
        # hermes_state.SessionDB.update_session_meta uses
        # ``model = COALESCE(?, model)`` — model=None leaves the column as-is.
        self.meta_calls.append(
            {
                "session_id": session_id,
                "config": json.loads(model_config_json),
                "model": model,
            }
        )


def _make_runner(db):
    from gateway.run import GatewayRunner

    runner = SimpleNamespace(_session_db=SimpleNamespace(_db=db))
    return GatewayRunner._sync_session_model_from_agent.__get__(runner)


def _agent(model, provider, *, fallback_active):
    return SimpleNamespace(
        model=model,
        provider=provider,
        base_url="http://localhost:11434/v1/" if fallback_active else "https://api.anthropic.com",
        api_mode="chat_completions",
        _fallback_activated=fallback_active,
    )


def test_fallback_turn_never_overwrites_session_model_column():
    """The core CRITICAL #25 latch: fallback model must not become the session model."""
    db = _FakeSessionDB(
        {
            "id": "s1",
            "model": "claude-opus-4-8",
            "model_config": json.dumps({"gateway_runtime": {}}),
        }
    )
    sync = _make_runner(db)

    sync("s1", _agent("qwen2.5:14b-instruct-q4_K_M", "custom", fallback_active=True))

    assert len(db.meta_calls) == 1
    call = db.meta_calls[0]
    # model=None → COALESCE keeps the user's selection in the DB.
    assert call["model"] is None
    runtime = call["config"]["gateway_runtime"]
    # Diagnostics still record what actually answered.
    assert runtime["fallback_active"] is True
    assert runtime["fallback_model"] == "qwen2.5:14b-instruct-q4_K_M"
    assert runtime["provider"] == "custom"


def test_primary_turn_still_persists_model_column():
    """Non-fallback turns keep the original sync behavior (model column updates)."""
    db = _FakeSessionDB(
        {
            "id": "s2",
            "model": "deepseek-v4-pro",
            "model_config": json.dumps({"gateway_runtime": {}}),
        }
    )
    sync = _make_runner(db)

    sync("s2", _agent("claude-opus-4-8", "anthropic", fallback_active=False))

    assert len(db.meta_calls) == 1
    call = db.meta_calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["config"]["gateway_runtime"]["fallback_active"] is False
    assert "fallback_model" not in call["config"]["gateway_runtime"]


def test_fallback_turn_is_change_detected_not_rewritten():
    """Same fallback runtime twice → second call is a no-op (no redundant write)."""
    row = {
        "id": "s3",
        "model": "claude-opus-4-8",
        "model_config": json.dumps(
            {
                "gateway_runtime": {
                    "provider": "custom",
                    "base_url": "http://localhost:11434/v1/",
                    "api_mode": "chat_completions",
                    "fallback_active": True,
                    "fallback_model": "qwen2.5:14b-instruct-q4_K_M",
                }
            }
        ),
    }
    db = _FakeSessionDB(row)
    sync = _make_runner(db)

    sync("s3", _agent("qwen2.5:14b-instruct-q4_K_M", "custom", fallback_active=True))

    assert db.meta_calls == []


def test_subscription_quota_400_gets_short_cooldown():
    """A 400-exhausted credential (Anthropic OAuth "out of extra usage") must
    recover in minutes, not the 1-hour default — the subscription's rolling
    window restores quota continuously and 400s carry no reset_at."""
    from agent.credential_pool import (
        EXHAUSTED_TTL_400_SECONDS,
        EXHAUSTED_TTL_DEFAULT_SECONDS,
        _exhausted_ttl,
    )

    assert _exhausted_ttl(400) == EXHAUSTED_TTL_400_SECONDS
    assert EXHAUSTED_TTL_400_SECONDS <= 10 * 60
    assert EXHAUSTED_TTL_400_SECONDS < EXHAUSTED_TTL_DEFAULT_SECONDS
    # Unrelated codes keep their existing cooldowns.
    assert _exhausted_ttl(402) == EXHAUSTED_TTL_DEFAULT_SECONDS
    assert _exhausted_ttl(None) == EXHAUSTED_TTL_DEFAULT_SECONDS


def test_reasoning_override_disables_thinking_for_local_qwen():
    """Config-side half of the 'does not support thinking' fix: with the
    live config shape (global reasoning_effort: high + per-model false
    override), the qwen fallback/selection resolves to thinking-disabled."""
    from hermes_constants import resolve_reasoning_config

    cfg = {
        "agent": {
            "reasoning_effort": "high",
            "reasoning_overrides": {"qwen2.5:14b-instruct-q4_K_M": False},
        }
    }

    resolved = resolve_reasoning_config(cfg, "qwen2.5:14b-instruct-q4_K_M")
    assert resolved is not None
    assert resolved.get("enabled") is False

    # Sanity: a model WITHOUT an override still resolves to enabled/high —
    # the override is per-model, not a global off-switch.
    other = resolve_reasoning_config(cfg, "claude-opus-4-8")
    assert other is not None
    assert other.get("enabled") is True
