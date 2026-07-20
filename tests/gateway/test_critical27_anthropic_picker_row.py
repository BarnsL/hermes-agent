"""Regression tests for CRITICAL #27 (2026-07-19): Anthropic vanishes from the picker.

Incident: the desktop model picker showed ten provider rows and no Anthropic
at all, so no Claude model could be selected — while the subscription token was
perfectly valid (``resolve_anthropic_token()`` returned a 108-char
``sk-ant-oat01-...`` the whole time).

Root cause was a credential gate that resolved only ``os.environ``. Claude Code
persists the setup token as a Windows USER environment variable (under
``HKCU\\Environment``); a process inherits it only if every ancestor of its
launch chain did. A backend spawned from a stripped/curated environment saw
none of ANTHROPIC_API_KEY / ANTHROPIC_TOKEN / CLAUDE_CODE_OAUTH_TOKEN, so
``list_authenticated_providers()`` dropped the provider row *before* building
any model list. Everything downstream was healthy but unreachable, which is why
the symptom looked like "the model table is empty" and kept recurring: the row
survived only when some launcher ancestor happened to propagate the user var.

Two layers had to be fixed, and both are pinned here:

1. ``model_switch._has_resolvable_provider_token`` — the listing gate now falls
   back to the documented resolution order (env -> HKCU registry -> credential
   files) instead of ``os.environ`` alone.
2. ``auth.is_provider_explicitly_configured`` — every ``explicit_only`` surface
   (the desktop chat picker passes ``explicit_only: true``) excluded
   CLAUDE_CODE_OAUTH_TOKEN as "ambient". A token merely inherited in the
   process env still is; one persisted under HKCU\\Environment is durable user
   configuration and must count, or the row gets filtered out again one layer
   later.

Layer 3 is the offline floor: the static table must carry the current model
aliases so a gateway restart with no network still yields a non-empty list.

If a hermes update reverts any of them, this file fails loudly.
"""

from __future__ import annotations

import pytest

ANTHROPIC_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

# Shape-accurate stand-in for a Claude subscription setup token. Not a real
# credential — only its prefix and length matter to the code under test.
FAKE_OAUTH_TOKEN = "sk-ant-oat01-" + ("A" * 95)


@pytest.fixture
def stripped_env(monkeypatch):
    """The exact broken state: no Anthropic variable anywhere in the process env."""
    for var in ANTHROPIC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_listing_gate_accepts_token_resolved_outside_environ(stripped_env, monkeypatch):
    """Layer 1: an out-of-environ token must still emit the Anthropic row."""
    import agent.anthropic_adapter as adapter
    from hermes_cli import model_switch

    monkeypatch.setattr(adapter, "resolve_anthropic_token", lambda: FAKE_OAUTH_TOKEN)

    assert model_switch._has_resolvable_provider_token("anthropic") is True

    rows = model_switch.list_authenticated_providers()
    anthropic = [r for r in rows if str(r.get("slug", "")).lower() == "anthropic"]
    assert anthropic, (
        "Anthropic row missing while resolve_anthropic_token() returns a valid "
        "token — the credential gate regressed to os.environ only (CRITICAL #27)"
    )
    assert anthropic[0]["models"], "Anthropic row emitted with an empty model list"


def test_listing_gate_still_drops_provider_with_no_token(stripped_env, monkeypatch):
    """The fix must not make the row unconditional — no token still means no row."""
    import agent.anthropic_adapter as adapter
    from hermes_cli import model_switch

    monkeypatch.setattr(adapter, "resolve_anthropic_token", lambda: None)

    assert model_switch._has_resolvable_provider_token("anthropic") is False


def test_resolver_failure_does_not_break_the_picker(stripped_env, monkeypatch):
    """A broken adapter must degrade to "no row", never take down provider listing."""
    import agent.anthropic_adapter as adapter
    from hermes_cli import model_switch

    def _boom():
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(adapter, "resolve_anthropic_token", _boom)

    assert model_switch._has_resolvable_provider_token("anthropic") is False
    assert model_switch.list_authenticated_providers()  # other providers survive


def test_registry_backed_oauth_token_counts_as_explicit(stripped_env, monkeypatch):
    """Layer 2: explicit_only surfaces must keep a durable HKCU-backed token.

    Without this, the desktop chat picker (explicit_only=1) filters the
    Anthropic row back out even after layer 1 restores it.
    """
    from hermes_cli import auth

    # Neutralize the other paths so this asserts the env-var branch alone.
    monkeypatch.setattr(auth, "_load_auth_store", lambda: {})
    monkeypatch.setattr(auth, "read_credential_pool", lambda _p: [])
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: {})

    monkeypatch.setattr(
        auth, "_user_env_registry_secret", lambda name: name == "CLAUDE_CODE_OAUTH_TOKEN"
    )
    assert auth.is_provider_explicitly_configured("anthropic") is True

    # An ambient token (present in the process env but NOT persisted under
    # HKCU\Environment) stays excluded — that is a live Claude Code parent
    # leaking its own credential, not the user configuring Hermes.
    monkeypatch.setattr(auth, "_user_env_registry_secret", lambda name: False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", FAKE_OAUTH_TOKEN)
    assert auth.is_provider_explicitly_configured("anthropic") is False


def test_static_table_survives_restart_with_no_network():
    """Layer 3: the offline floor must carry the current model aliases."""
    from hermes_cli.models import _PROVIDER_MODELS

    floor = [m.lower() for m in _PROVIDER_MODELS.get("anthropic", [])]
    assert floor, "static anthropic table is empty — a cold restart lists no models"
    for expected in (
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
    ):
        assert expected in floor, f"{expected} missing from the offline model floor"


def test_subscription_context_cap_is_intact_and_opt_in_works(monkeypatch):
    """CRITICAL #26 guard: 200K stays the default; long_context is the only escape.

    The 1M window is real, so a well-meaning "fix" that removes the clamp would
    look correct and silently bill every >200K request to the non-refilling
    extra-usage lane. Pin both directions.
    """
    import agent.model_metadata as mm

    monkeypatch.setattr(
        "agent.anthropic_adapter.resolve_anthropic_token", lambda: FAKE_OAUTH_TOKEN
    )

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda *a, **k: {})
    assert (
        mm._apply_subscription_context_cap(1_000_000, "anthropic", FAKE_OAUTH_TOKEN)
        == 200_000
    )

    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda *a, **k: {"anthropic": {"long_context": True}},
    )
    assert (
        mm._apply_subscription_context_cap(1_000_000, "anthropic", FAKE_OAUTH_TOKEN)
        == 1_000_000
    ), "anthropic.long_context escape hatch stopped working"

    # A metered API key has no extra-usage lane to protect — never capped.
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda *a, **k: {})
    assert (
        mm._apply_subscription_context_cap(1_000_000, "anthropic", "sk-ant-api03-xyz")
        == 1_000_000
    )
