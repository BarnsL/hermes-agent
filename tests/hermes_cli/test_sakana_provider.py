"""Tests for Sakana AI provider support — standard direct API provider.

Sakana AI serves the Fugu multi-agent orchestration family over an
OpenAI-compatible endpoint at ``https://api.sakana.ai/v1``.
"""

import types

import pytest

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    resolve_provider,
    get_api_key_provider_status,
    resolve_api_key_provider_credentials,
)


_OTHER_PROVIDER_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
    "XAI_API_KEY", "KIMI_API_KEY", "KIMI_CN_API_KEY",
    "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
    "KILOCODE_API_KEY", "HF_TOKEN", "GLM_API_KEY", "ZAI_API_KEY",
    "XIAOMI_API_KEY", "TOKENHUB_API_KEY", "ARCEEAI_API_KEY",
    "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
)


# =============================================================================
# Provider Registry
# =============================================================================


class TestSakanaProviderRegistry:
    def test_registered(self):
        assert "sakana" in PROVIDER_REGISTRY

    def test_name(self):
        assert PROVIDER_REGISTRY["sakana"].name == "Sakana AI"

    def test_auth_type(self):
        assert PROVIDER_REGISTRY["sakana"].auth_type == "api_key"

    def test_inference_base_url(self):
        assert PROVIDER_REGISTRY["sakana"].inference_base_url == "https://api.sakana.ai/v1"

    def test_api_key_env_vars(self):
        assert PROVIDER_REGISTRY["sakana"].api_key_env_vars == ("SAKANA_API_KEY",)

    def test_base_url_env_var(self):
        assert PROVIDER_REGISTRY["sakana"].base_url_env_var == "SAKANA_BASE_URL"


# =============================================================================
# Aliases
# =============================================================================


class TestSakanaAliases:
    @pytest.mark.parametrize("alias", ["sakana", "sakana-ai", "sakanaai"])
    def test_alias_resolves(self, alias, monkeypatch):
        for key in _OTHER_PROVIDER_KEYS + ("OPENROUTER_API_KEY",):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("SAKANA_API_KEY", "sakana-test-12345")
        assert resolve_provider(alias) == "sakana"

    def test_normalize_provider_models_py(self):
        from hermes_cli.models import normalize_provider
        assert normalize_provider("sakana-ai") == "sakana"
        assert normalize_provider("sakanaai") == "sakana"

    def test_normalize_provider_providers_py(self):
        from hermes_cli.providers import normalize_provider
        assert normalize_provider("sakana-ai") == "sakana"
        assert normalize_provider("sakanaai") == "sakana"


# =============================================================================
# Credentials
# =============================================================================


class TestSakanaCredentials:
    def test_status_configured(self, monkeypatch):
        monkeypatch.setenv("SAKANA_API_KEY", "sakana-test")
        status = get_api_key_provider_status("sakana")
        assert status["configured"]

    def test_status_not_configured(self, monkeypatch):
        monkeypatch.delenv("SAKANA_API_KEY", raising=False)
        status = get_api_key_provider_status("sakana")
        assert not status["configured"]

    def test_openrouter_key_does_not_make_sakana_configured(self, monkeypatch):
        """OpenRouter users should NOT see sakana as configured."""
        monkeypatch.delenv("SAKANA_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        status = get_api_key_provider_status("sakana")
        assert not status["configured"]

    def test_resolve_credentials(self, monkeypatch):
        monkeypatch.setenv("SAKANA_API_KEY", "sakana-direct-key")
        monkeypatch.delenv("SAKANA_BASE_URL", raising=False)
        creds = resolve_api_key_provider_credentials("sakana")
        assert creds["api_key"] == "sakana-direct-key"
        assert creds["base_url"] == "https://api.sakana.ai/v1"

    def test_custom_base_url_override(self, monkeypatch):
        monkeypatch.setenv("SAKANA_API_KEY", "sakana-x")
        monkeypatch.setenv("SAKANA_BASE_URL", "https://custom.sakana.example/v1")
        creds = resolve_api_key_provider_credentials("sakana")
        assert creds["base_url"] == "https://custom.sakana.example/v1"


# =============================================================================
# Model catalog
# =============================================================================


class TestSakanaModelCatalog:
    def test_static_model_list(self):
        """Sakana has a static _PROVIDER_MODELS catalog entry. Specific model
        names change with releases and don't belong in tests.
        """
        from hermes_cli.models import _PROVIDER_MODELS
        assert "sakana" in _PROVIDER_MODELS
        assert len(_PROVIDER_MODELS["sakana"]) >= 1

    def test_canonical_provider_entry(self):
        from hermes_cli.models import CANONICAL_PROVIDERS
        slugs = [p.slug for p in CANONICAL_PROVIDERS]
        assert "sakana" in slugs


# =============================================================================
# Model normalization
# =============================================================================


class TestSakanaNormalization:
    def test_in_matching_prefix_strip_set(self):
        from hermes_cli.model_normalize import _MATCHING_PREFIX_STRIP_PROVIDERS
        assert "sakana" in _MATCHING_PREFIX_STRIP_PROVIDERS

    def test_strips_prefix(self):
        from hermes_cli.model_normalize import normalize_model_for_provider
        assert normalize_model_for_provider("sakana/fugu-ultra", "sakana") == "fugu-ultra"

    def test_bare_name_unchanged(self):
        from hermes_cli.model_normalize import normalize_model_for_provider
        assert normalize_model_for_provider("fugu-ultra", "sakana") == "fugu-ultra"


# =============================================================================
# URL mapping
# =============================================================================


class TestSakanaURLMapping:
    def test_url_to_provider(self):
        from agent.model_metadata import _URL_TO_PROVIDER
        assert _URL_TO_PROVIDER.get("api.sakana.ai") == "sakana"

    def test_provider_prefixes(self):
        from agent.model_metadata import _PROVIDER_PREFIXES
        assert "sakana" in _PROVIDER_PREFIXES
        assert "sakana-ai" in _PROVIDER_PREFIXES
        assert "sakanaai" in _PROVIDER_PREFIXES

    def test_trajectory_compressor_detects_sakana(self):
        import trajectory_compressor as tc
        comp = tc.TrajectoryCompressor.__new__(tc.TrajectoryCompressor)
        comp.config = types.SimpleNamespace(base_url="https://api.sakana.ai/v1")
        assert comp._detect_provider() == "sakana"


# =============================================================================
# Context window
# =============================================================================


class TestSakanaContextWindow:
    @pytest.mark.parametrize(
        "model", ["fugu", "fugu-ultra", "fugu-ultra-20260615"]
    )
    def test_fugu_family_is_one_million(self, model):
        """The single ``fugu`` substring entry must cover the whole family.

        Lookup is longest-substring-first, so one entry serves fugu,
        fugu-ultra and dated snapshots alike.
        """
        from agent.model_metadata import DEFAULT_CONTEXT_LENGTHS
        match = next(
            length
            for key, length in sorted(
                DEFAULT_CONTEXT_LENGTHS.items(),
                key=lambda kv: len(kv[0]),
                reverse=True,
            )
            if key in model
        )
        assert match == 1_000_000


# =============================================================================
# providers.py overlay + aliases
# =============================================================================


class TestSakanaProvidersModule:
    def test_overlay_exists(self):
        from hermes_cli.providers import HERMES_OVERLAYS
        assert "sakana" in HERMES_OVERLAYS
        overlay = HERMES_OVERLAYS["sakana"]
        assert overlay.transport == "openai_chat"
        assert overlay.base_url_env_var == "SAKANA_BASE_URL"
        assert not overlay.is_aggregator

    def test_label(self):
        from hermes_cli.models import _PROVIDER_LABELS
        assert _PROVIDER_LABELS["sakana"] == "Sakana AI"


# =============================================================================
# Auxiliary client — main-model-first design
# =============================================================================


class TestSakanaAuxiliary:
    def test_main_model_first_design(self):
        """Sakana uses main-model-first — no _API_KEY_PROVIDER_AUX_MODELS entry."""
        from agent.auxiliary_client import _API_KEY_PROVIDER_AUX_MODELS
        assert "sakana" not in _API_KEY_PROVIDER_AUX_MODELS
