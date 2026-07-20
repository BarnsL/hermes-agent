"""Sakana AI provider profile."""

from providers import register_provider
from providers.base import ProviderProfile

sakana = ProviderProfile(
    name="sakana",
    aliases=("sakana-ai", "sakanaai"),
    env_vars=("SAKANA_API_KEY",),
    base_url="https://api.sakana.ai/v1",
    display_name="Sakana AI",
    description="Sakana AI (Fugu multi-agent orchestration)",
    signup_url="https://console.sakana.ai",
    api_mode="chat_completions",
    supports_health_check=True,
    fallback_models=("fugu-ultra", "fugu"),
)

register_provider(sakana)
