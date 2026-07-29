"""Regression coverage for deterministic Discord mention repair."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter
from plugins.platforms.discord.mention_resolution import (
    MentionCandidate,
    repair_outbound_mentions,
)


@pytest.mark.asyncio
async def test_valid_numeric_mentions_and_mass_mentions_are_unchanged():
    content = "Hi <@1234567890> @everyone @here <@&2222222222>"
    result = await repair_outbound_mentions(content)

    assert result.content == content
    assert not result.changed
    assert result.lookup_attempts == 0


@pytest.mark.asyncio
async def test_broken_placeholder_repairs_from_single_inbound_target():
    target = MentionCandidate(
        user_id="123456789012345678",
        names=("AstroDoodz", "Astro"),
    )
    result = await repair_outbound_mentions(
        "Tell <@[ID]> the scan is ready.",
        hint_candidates=(target,),
    )

    assert result.content == "Tell <@123456789012345678> the scan is ready."
    assert result.repaired_placeholders == 1


@pytest.mark.asyncio
async def test_ambiguous_placeholder_degrades_to_non_pinging_text():
    candidates = (
        MentionCandidate("123456789012345678", ("Alex",)),
        MentionCandidate("223456789012345678", ("Alex Two",)),
    )
    result = await repair_outbound_mentions(
        "Tell <@> the scan is ready.",
        hint_candidates=candidates,
    )

    assert result.content == "Tell [unresolved user] the scan is ready."
    assert "<@" not in result.content


@pytest.mark.asyncio
async def test_plain_name_uses_exact_inbound_hint_without_lookup():
    target = MentionCandidate("123456789012345678", ("AstroDoodz",))
    called = False

    async def lookup(_query):
        nonlocal called
        called = True
        return ()

    result = await repair_outbound_mentions(
        "Hi @AstroDoodz.",
        hint_candidates=(target,),
        lookup=lookup,
    )

    assert result.content == "Hi <@123456789012345678>."
    assert result.lookup_attempts == 0
    assert called is False


@pytest.mark.asyncio
async def test_lookup_is_bounded_and_ambiguous_names_do_not_ping():
    calls = []

    async def lookup(query):
        calls.append(query)
        if query == "AstroDoodz":
            return (
                MentionCandidate(
                    "123456789012345678",
                    ("AstroDoodz",),
                ),
            )
        if query == "Alex":
            return (
                MentionCandidate("223456789012345678", ("Alex",)),
                MentionCandidate("323456789012345678", ("Alex",)),
            )
        return ()

    result = await repair_outbound_mentions(
        "@AstroDoodz @Alex @Missing",
        lookup=lookup,
        max_lookup_attempts=2,
    )

    assert result.content == "<@123456789012345678> @Alex @Missing"
    assert calls == ["AstroDoodz", "Alex"]
    assert result.lookup_attempts == 2


@pytest.mark.asyncio
async def test_code_and_mass_mentions_are_never_expanded():
    target = MentionCandidate("123456789012345678", ("AstroDoodz",))
    content = "@everyone @here `@AstroDoodz` ```\n<@[ID]>\n```"

    result = await repair_outbound_mentions(
        content,
        hint_candidates=(target,),
    )

    assert result.content == content


@pytest.mark.asyncio
async def test_fake_reply_directive_is_removed_before_delivery():
    result = await repair_outbound_mentions(
        "[REPLY TO DISCORD MESSAGE [ID]] Hello there"
    )

    assert result.content == " Hello there"


@pytest.mark.asyncio
async def test_adapter_capture_repairs_placeholder_on_real_send_boundary():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    sent = SimpleNamespace(id=987654321)
    channel = SimpleNamespace(
        id=555,
        guild=None,
        send=AsyncMock(return_value=sent),
    )
    bot_member = SimpleNamespace(
        id=999999999999999999,
        name="Nous Man",
        display_name="Nous Man",
        global_name=None,
        nick=None,
    )
    target = SimpleNamespace(
        id=123456789012345678,
        name="astrodoodz",
        display_name="AstroDoodz",
        global_name=None,
        nick=None,
    )
    adapter._client = SimpleNamespace(
        user=bot_member,
        get_channel=lambda _channel_id: channel,
        fetch_channel=AsyncMock(),
    )
    inbound = SimpleNamespace(
        id=222222222222222222,
        mentions=(bot_member, target),
    )
    adapter._remember_message_mentions(inbound, "555")

    result = await adapter.send("555", "Tell <@[ID]> the scan is ready.")

    assert result.success is True
    channel.send.assert_awaited_once_with(
        content="Tell <@123456789012345678> the scan is ready.",
        reference=None,
    )


@pytest.mark.asyncio
async def test_adapter_edit_self_repairs_plain_name_from_guild_cache():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    target = SimpleNamespace(
        id=123456789012345678,
        name="astrodoodz",
        display_name="AstroDoodz",
        global_name=None,
        nick=None,
    )
    edited_message = SimpleNamespace(edit=AsyncMock())
    guild = SimpleNamespace(members=(target,))
    channel = SimpleNamespace(
        id=555,
        guild=guild,
        fetch_message=AsyncMock(return_value=edited_message),
    )
    adapter._client = SimpleNamespace(
        get_channel=lambda _channel_id: channel,
        fetch_channel=AsyncMock(),
    )
    # Make sure this test exercises member lookup, not a previous channel hint.
    adapter._mention_hints_by_channel.clear()
    adapter._mention_hints_by_message.clear()

    result = await adapter.edit_message(
        "555",
        "777",
        "Hello @AstroDoodz.",
        finalize=True,
    )

    assert result.success is True
    edited_message.edit.assert_awaited_once_with(
        content="Hello <@123456789012345678>."
    )
