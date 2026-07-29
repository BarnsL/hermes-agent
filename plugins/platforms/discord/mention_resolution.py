"""Deterministic outbound Discord mention resolution.

PURPLE-DISCORD-MENTIONS-2026-07-29

The language model may emit a valid Discord mention (``<@123...>``), a plain
name (``@DisplayName``), or a broken placeholder such as ``<@[ID]>``.  This
module converts only exact, unambiguous user matches to numeric Discord
mentions.  It deliberately never expands roles, ``@everyone``, or ``@here``.

The adapter supplies candidates from the triggering Discord message first and
may provide one bounded guild lookup callback for names that were not present
in that message.  Unresolved text remains non-pinging text instead of becoming
an incorrect mention or a hard send failure.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence


_VALID_USER_MENTION_RE = re.compile(r"<@!?([0-9]{5,25})>")
_BROKEN_USER_MENTION_RE = re.compile(
    r"<@\s*(?:!?\s*)?(?:\[?\s*(?:ID|USER_ID|USERID|MEMBER_ID)\s*\]?)?\s*>",
    re.IGNORECASE,
)
_BROKEN_REPLY_DIRECTIVE_RE = re.compile(
    r"\[\s*REPLY\s+TO\s+DISCORD\s+MESSAGE\s+"
    r"(?:\[?\s*(?:ID|MESSAGE_ID)\s*\]?|<@\s*>)\s*\]",
    re.IGNORECASE,
)
_PLAIN_USER_MENTION_RE = re.compile(
    r"(?<![<@\w])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,30}[A-Za-z0-9_])?)"
)
_CODE_SPLIT_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
_NEVER_RESOLVE = frozenset({"everyone", "here"})


@dataclass(frozen=True)
class MentionCandidate:
    """A Discord user ID plus every exact name that may identify that user."""

    user_id: str
    names: tuple[str, ...]

    @classmethod
    def from_member(cls, member: Any) -> Optional["MentionCandidate"]:
        """Build a candidate from a discord.py Member/User or a test double."""

        raw_id = str(getattr(member, "id", "") or "").strip()
        if not raw_id.isdigit():
            return None

        names: list[str] = []
        for attr in ("name", "display_name", "global_name", "nick"):
            value = str(getattr(member, attr, "") or "").strip()
            if value and value not in names:
                names.append(value)
        return cls(user_id=raw_id, names=tuple(names))


@dataclass(frozen=True)
class MentionRepairResult:
    """Repair output plus audit counters suitable for privacy-safe logging."""

    content: str
    repaired_placeholders: int = 0
    resolved_names: int = 0
    unresolved_names: tuple[str, ...] = ()
    lookup_attempts: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.repaired_placeholders or self.resolved_names)


LookupCallback = Callable[
    [str],
    Awaitable[Sequence[MentionCandidate]] | Sequence[MentionCandidate],
]


def _canonical_name(value: str) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def _dedupe_candidates(
    candidates: Iterable[MentionCandidate],
) -> tuple[MentionCandidate, ...]:
    by_id: dict[str, set[str]] = {}
    original_names: dict[str, list[str]] = {}
    for candidate in candidates:
        user_id = str(candidate.user_id or "").strip()
        if not user_id.isdigit():
            continue
        by_id.setdefault(user_id, set())
        original_names.setdefault(user_id, [])
        for name in candidate.names:
            canonical = _canonical_name(name)
            if not canonical or canonical in by_id[user_id]:
                continue
            by_id[user_id].add(canonical)
            original_names[user_id].append(str(name).strip())
    return tuple(
        MentionCandidate(user_id=user_id, names=tuple(original_names[user_id]))
        for user_id in by_id
    )


def _exact_matches(
    query: str,
    candidates: Iterable[MentionCandidate],
) -> tuple[MentionCandidate, ...]:
    wanted = _canonical_name(query)
    if not wanted or wanted in _NEVER_RESOLVE:
        return ()
    return tuple(
        candidate
        for candidate in _dedupe_candidates(candidates)
        if wanted in {_canonical_name(name) for name in candidate.names}
    )


def candidates_from_members(
    members: Iterable[Any],
    *,
    exclude_user_id: Optional[str] = None,
) -> tuple[MentionCandidate, ...]:
    """Convert Discord members to unique candidates, excluding the bot itself."""

    excluded = str(exclude_user_id or "")
    candidates: list[MentionCandidate] = []
    for member in members or ():
        candidate = MentionCandidate.from_member(member)
        if candidate is not None and candidate.user_id != excluded:
            candidates.append(candidate)
    return _dedupe_candidates(candidates)


async def _call_lookup(
    callback: Optional[LookupCallback],
    query: str,
) -> tuple[MentionCandidate, ...]:
    if callback is None:
        return ()
    result = callback(query)
    if inspect.isawaitable(result):
        result = await result
    return _dedupe_candidates(result or ())


async def repair_outbound_mentions(
    content: str,
    *,
    hint_candidates: Iterable[MentionCandidate] = (),
    lookup: Optional[LookupCallback] = None,
    max_lookup_attempts: int = 3,
    max_user_mentions: int = 5,
) -> MentionRepairResult:
    """Repair model-authored user mentions without broadening ping privileges.

    Resolution order is deterministic:

    1. Preserve valid numeric user mentions.
    2. Repair a broken placeholder only when the triggering message identifies
       exactly one non-bot user.
    3. Resolve a plain ``@username`` against exact triggering-message names.
    4. If still missing, perform at most ``max_lookup_attempts`` exact guild
       lookups.
    5. Leave ambiguous or missing names as plain, non-pinging text.

    Markdown code spans/fences are copied verbatim so examples are not turned
    into live pings.  Discord role/everyone/here syntax is never generated.
    """

    text = str(content or "")
    hints = _dedupe_candidates(hint_candidates)
    existing_ids = {
        match.group(1) for match in _VALID_USER_MENTION_RE.finditer(text)
    }
    ping_ids = set(existing_ids)
    repaired_placeholders = 0
    resolved_names = 0
    unresolved_names: list[str] = []
    lookup_attempts = 0
    lookup_cache: dict[str, tuple[MentionCandidate, ...]] = {}

    unique_hint = hints[0] if len(hints) == 1 else None

    def replace_broken(segment: str) -> str:
        nonlocal repaired_placeholders

        def replacement(_match: re.Match[str]) -> str:
            nonlocal repaired_placeholders
            if (
                unique_hint is not None
                and (
                    unique_hint.user_id in ping_ids
                    or len(ping_ids) < max(0, max_user_mentions)
                )
            ):
                ping_ids.add(unique_hint.user_id)
                repaired_placeholders += 1
                return f"<@{unique_hint.user_id}>"
            # Never leak the model's invalid Discord syntax to users.
            return "[unresolved user]"

        return _BROKEN_USER_MENTION_RE.sub(replacement, segment)

    async def repair_plain(segment: str) -> str:
        nonlocal lookup_attempts, resolved_names
        output: list[str] = []
        cursor = 0
        for match in _PLAIN_USER_MENTION_RE.finditer(segment):
            output.append(segment[cursor : match.start()])
            raw_name = match.group(1)
            canonical = _canonical_name(raw_name)
            replacement = match.group(0)

            if canonical not in _NEVER_RESOLVE:
                matches = _exact_matches(raw_name, hints)
                if not matches and canonical not in lookup_cache:
                    if lookup is not None and lookup_attempts < max(0, max_lookup_attempts):
                        lookup_attempts += 1
                        lookup_cache[canonical] = await _call_lookup(lookup, raw_name)
                    else:
                        lookup_cache[canonical] = ()
                if not matches:
                    matches = _exact_matches(
                        raw_name,
                        lookup_cache.get(canonical, ()),
                    )

                if (
                    len(matches) == 1
                    and (
                        matches[0].user_id in ping_ids
                        or len(ping_ids) < max(0, max_user_mentions)
                    )
                ):
                    user_id = matches[0].user_id
                    ping_ids.add(user_id)
                    replacement = f"<@{user_id}>"
                    resolved_names += 1
                elif len(matches) != 1:
                    unresolved_names.append(raw_name)

            output.append(replacement)
            cursor = match.end()
        output.append(segment[cursor:])
        return "".join(output)

    parts = _CODE_SPLIT_RE.split(text)
    repaired_parts: list[str] = []
    for index, part in enumerate(parts):
        if index % 2:
            repaired_parts.append(part)
            continue
        # Reply directives are model-side control artifacts, not Discord
        # syntax.  The adapter already applies the real reply reference.
        part = _BROKEN_REPLY_DIRECTIVE_RE.sub("", part)
        repaired_parts.append(await repair_plain(replace_broken(part)))

    return MentionRepairResult(
        content="".join(repaired_parts),
        repaired_placeholders=repaired_placeholders,
        resolved_names=resolved_names,
        unresolved_names=tuple(dict.fromkeys(unresolved_names)),
        lookup_attempts=lookup_attempts,
    )


__all__ = [
    "MentionCandidate",
    "MentionRepairResult",
    "candidates_from_members",
    "repair_outbound_mentions",
]
