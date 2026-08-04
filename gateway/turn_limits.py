"""Resolve hard gateway turn limits without conflating them with inactivity.

The gateway's existing ``agent.gateway_timeout`` is intentionally based on
*inactivity*: a long task may keep running while tools or stream heartbeats
continue to report progress.  That is the wrong safety primitive for chat
surfaces when an overloaded provider keeps a stream technically alive but
never completes the user's turn.  Platform limits add an optional wall-clock
deadline and a lower iteration ceiling without changing the global defaults.

Configuration lives under ``agent.platform_limits``::

    agent:
      platform_limits:
        discord:
          max_turns: 12
          wall_timeout: 180

``max_turns`` can only tighten the global ``agent.max_turns`` budget.  A
non-positive ``wall_timeout`` disables the hard deadline for that platform.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class GatewayTurnLimits:
    """Effective limits for one gateway platform turn."""

    max_iterations: int
    wall_timeout_seconds: Optional[float]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def resolve_gateway_turn_limits(
    config: Mapping[str, Any],
    platform: Any,
    *,
    default_max_iterations: int,
) -> GatewayTurnLimits:
    """Return the effective iteration cap and wall deadline for ``platform``.

    Invalid or absent platform values fail open to the established global
    iteration budget and no wall-clock deadline.  The platform iteration cap
    is intentionally a ceiling, not an override that can enlarge the global
    budget.
    """

    global_max = _positive_int(default_max_iterations) or 1
    platform_name = str(getattr(platform, "value", platform) or "").strip().lower()

    agent_config = _as_mapping(_as_mapping(config).get("agent"))
    platform_limits = _as_mapping(agent_config.get("platform_limits"))
    selected = _as_mapping(platform_limits.get(platform_name))

    configured_max = _positive_int(selected.get("max_turns"))
    effective_max = min(global_max, configured_max) if configured_max else global_max

    configured_wall = _nonnegative_float(selected.get("wall_timeout"))
    wall_timeout = configured_wall if configured_wall and configured_wall > 0 else None

    return GatewayTurnLimits(
        max_iterations=effective_max,
        wall_timeout_seconds=wall_timeout,
    )


def wall_timeout_expired(
    started_monotonic: float,
    timeout_seconds: Optional[float],
    *,
    now_monotonic: Optional[float] = None,
) -> bool:
    """Return whether a configured hard deadline has elapsed."""

    if timeout_seconds is None or timeout_seconds <= 0:
        return False
    now = time.monotonic() if now_monotonic is None else now_monotonic
    return now - started_monotonic >= timeout_seconds
