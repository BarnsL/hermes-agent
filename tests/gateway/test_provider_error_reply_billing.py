"""Regression tests: provider quota/credit exhaustion gets its own user reply.

Live failure 2026-07-19. A Discord user asked the bot a question and got, twice
in a row:

    "The model provider failed after retries. I kept raw provider details out
     of chat; check gateway logs for diagnostics."

The real cause was the Claude-subscription lane answering

    HTTP 400: You're out of extra usage. Add more at claude.ai/settings/usage
    and keep going.

``_gateway_provider_error_reply`` re-derives the error class from the message
TEXT, and that string matched none of its buckets: it is not an auth error, not
a policy rejection, and — critically — not a rate limit, because
``_GATEWAY_RATE_LIMIT_RE`` looks for "usage limit"/"quota"/429 and Anthropic
says "extra usage" with a 400. So it fell through to the generic default, which
was wrong twice over: it blamed retries for an error classified
``retryable=False`` (no retry ever ran), and it gave the user no idea their
Claude quota was spent.

These tests pin the billing bucket, its precedence over the rate-limit bucket
(both contain the word "usage"), and the fact that a genuine 429 is still
reported as a retry-soon condition.
"""

from __future__ import annotations

import pytest

# The exact string from the 2026-07-19 Discord incident, as
# AIAgent._summarize_api_error renders it into final_response.
LIVE_ANTHROPIC_QUOTA = (
    "HTTP 400: You're out of extra usage. "
    "Add more at claude.ai/settings/usage and keep going."
)


def _reply(text: str) -> str:
    from gateway.run import _gateway_provider_error_reply

    return _gateway_provider_error_reply(text)


def test_anthropic_subscription_quota_gets_billing_reply():
    """The live failure string must produce billing copy, not the generic one."""
    reply = _reply(LIVE_ANTHROPIC_QUOTA)
    assert "quota or credit" in reply
    # The old generic default must NOT be what this error produces: it is
    # factually wrong (retryable=False means no retries were attempted).
    assert "failed after retries" not in reply


def test_billing_reply_does_not_tell_user_to_just_retry():
    """A spent balance does not clear on its own the way a 429 does."""
    reply = _reply(LIVE_ANTHROPIC_QUOTA)
    assert "wait a moment and try again" not in reply


@pytest.mark.parametrize(
    "text",
    [
        LIVE_ANTHROPIC_QUOTA,
        # z.ai / GLM, observed live 2026-07-19 (HTTP 429, code 1113) — a 429
        # that is really a billing wall, which is why billing is checked first.
        "Insufficient balance or no resource package. Please recharge.",
        "HTTP 402: Payment required",
        "Error code: 429 - insufficient_quota",
        "Your credit balance is too low to access the Anthropic API",
    ],
)
def test_quota_and_credit_errors_all_route_to_billing(text):
    assert "quota or credit" in _reply(text)


@pytest.mark.parametrize(
    "text",
    [
        "HTTP 429: Rate limit exceeded, please slow down",
        "Error code: 429 - rate_limit_error",
    ],
)
def test_plain_rate_limits_still_report_as_retry_soon(text):
    """Billing must not swallow ordinary throttling — that one IS retry-soon."""
    reply = _reply(text)
    assert "rate-limiting requests" in reply
    assert "quota or credit" not in reply


def test_auth_errors_are_unchanged():
    reply = _reply("HTTP 401: invalid api key")
    assert "authentication failed" in reply.lower()


def test_unclassified_errors_still_hit_the_generic_default():
    reply = _reply("HTTP 500: internal server error")
    assert "failed after retries" in reply
