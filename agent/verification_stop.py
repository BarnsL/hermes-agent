"""Turn-end verification guard for coding edits.

This module is intentionally policy-only. It never runs checks itself; it turns
the passive verification ledger into a bounded follow-up when the model tries to
finish immediately after editing code without fresh evidence.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


_MAX_CHANGED_PATHS_IN_NUDGE = 8

# CODING-HARNESS-REVIEW-2026-07-16 §3.1 (Codex parity, opt-in strict mode):
# the default soft gate below caps at 2 nudges and the model can talk past it
# by asserting a blocker — OpenAI Codex instead MANDATES "you MUST run all
# checks after edits". ``agent.verify_on_stop: "strict"`` hard-blocks turn
# completion after code edits until fresh passing evidence exists, re-issuing
# the continuation requirement up to this many attempts. The cap is the escape
# hatch: a genuinely blocked model (no runnable toolchain, sandboxed FS, broken
# suite it may not fix) must not spin forever, so at the cap the turn releases
# WITH a loud warning banner (``build_verify_on_stop_release_warning``) instead
# of silently shipping an unverified answer. 8 is deliberately generous: each
# attempt is a full model turn, enough to run a suite, read a failure, repair,
# and re-run several times over.
STRICT_VERIFY_MAX_ATTEMPTS = 8

# Non-code file extensions whose edits carry no verifiable runtime behavior:
# documentation, prose, and data/markup that no test/build exercises. When a
# turn touches ONLY these, verify-on-stop has nothing to check, so the nudge is
# suppressed (this is fix "C" for the doc/markdown/skill false-positive — a
# SKILL.md or README edit must never demand a /tmp verification script). A turn
# that edits any non-listed path (a real source/code/config file) still nudges.
_NON_CODE_VERIFY_EXTENSIONS = frozenset(
    {
        ".md",
        ".markdown",
        ".mdx",
        ".rst",
        ".txt",
        ".text",
        ".adoc",
        ".asciidoc",
        ".org",
        ".log",
        ".csv",
        ".tsv",
    }
)

# Filenames (case-insensitive, extension-less or otherwise) that are pure prose
# even without a recognized doc extension.
_NON_CODE_VERIFY_FILENAMES = frozenset(
    {
        "license",
        "licence",
        "notice",
        "authors",
        "contributors",
        "changelog",
        "codeowners",
    }
)


def _is_non_code_path(raw: str) -> bool:
    """Return True when a changed path is documentation/prose with nothing to verify."""
    try:
        p = Path(str(raw))
    except Exception:
        return False
    suffix = p.suffix.lower()
    if suffix in _NON_CODE_VERIFY_EXTENSIONS:
        return True
    if not suffix and p.name.lower() in _NON_CODE_VERIFY_FILENAMES:
        return True
    return False


def _filter_verifiable_paths(paths: Iterable[str]) -> list[str]:
    """Drop documentation/prose paths; keep paths that could have verifiable behavior."""
    return [p for p in paths if p and not _is_non_code_path(p)]


# Session identities (platform or source) that are NOT human conversational
# messaging surfaces: interactive coding surfaces (CLI, TUI, desktop, codex,
# local, gateway) and programmatic callers (API server, webhooks, tools).
# Verify-on-stop stays ON by default for these. Any other resolved gateway
# platform is a conversational messaging surface (Telegram, Discord, WhatsApp,
# Signal, Slack, etc.) where the verification narrative would reach a human as
# chat noise, so it defaults OFF. Mirrors LOCAL_SESSION_SOURCE_IDS in
# apps/desktop/src/lib/session-source.ts; keep roughly in sync when adding a
# local or programmatic surface. Default-deny by design: an unrecognized
# identity is treated as messaging (OFF) so a new chat platform never leaks the
# verification receipt before this set is updated.
_NON_MESSAGING_SESSION_SURFACES = frozenset(
    {
        "",
        "cli",
        "codex",
        "desktop",
        "gateway",
        "local",
        "tui",
        "tool",
        "api_server",
        "webhook",
        "msgraph_webhook",
    }
)


def _session_is_messaging_surface() -> bool:
    """Return whether this turn is delivered over a human messaging channel.

    The gateway binds the platform value (e.g. ``telegram``) to
    ``HERMES_SESSION_PLATFORM``; the CLI and TUI set ``HERMES_SESSION_SOURCE``
    (e.g. ``cli``, ``tui``) instead. Both are consulted via the session-context
    helper (with an ``os.environ`` fallback), alongside the ``HERMES_PLATFORM``
    override, matching the sibling platform resolution in
    ``agent/skill_commands.py`` and ``agent/prompt_builder.py``. A turn is a
    messaging surface when a resolved identity is present and is not a known
    non-messaging surface.
    """
    try:
        from gateway.session_context import get_session_env

        platform = (
            os.getenv("HERMES_PLATFORM")
            or get_session_env("HERMES_SESSION_PLATFORM", "")
        )
        source = get_session_env("HERMES_SESSION_SOURCE", "")
    except Exception:
        platform = os.getenv("HERMES_PLATFORM", "") or os.environ.get(
            "HERMES_SESSION_PLATFORM", ""
        )
        source = os.environ.get("HERMES_SESSION_SOURCE", "")
    for identity in (platform, source):
        identity = str(identity or "").strip().lower()
        if identity and identity not in _NON_MESSAGING_SESSION_SURFACES:
            return True
    return False


def verify_on_stop_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return whether edit -> verify-before-finish behavior is enabled.

    Precedence: an explicit ``HERMES_VERIFY_ON_STOP`` env var wins, then an
    explicit ``agent.verify_on_stop`` config value. The config default is
    ``"auto"`` (see ``DEFAULT_CONFIG``) — surface-aware: ON for interactive
    coding surfaces (CLI, TUI, desktop) and programmatic callers, OFF for
    conversational messaging surfaces (Telegram, Discord, etc.) where the
    verification narrative would reach a human as chat noise. An explicit
    bool forces the behavior in either direction. ``"strict"`` (the opt-in
    hard gate, CODING-HARNESS-REVIEW-2026-07-16 §3.1) forces ON everywhere,
    matching the explicit-``true`` precedent: an operator who opts into the
    hard gate wants it regardless of surface. A missing or unrecognized value
    falls back to the surface-aware ``"auto"`` default.
    """
    env = os.environ.get("HERMES_VERIFY_ON_STOP")
    if env is not None:
        # "strict" lands in the truthy branch here by construction (it is not
        # an off token), so HERMES_VERIFY_ON_STOP=strict both enables the gate
        # and selects strict mode via verify_on_stop_strict().
        return env.strip().lower() not in {"0", "false", "no", "off"}
    if config is None:
        try:
            from hermes_cli.config import load_config

            config = load_config()
        except Exception:
            config = {}
    agent_cfg = (config or {}).get("agent") if isinstance(config, dict) else None
    cfg_val = agent_cfg.get("verify_on_stop") if isinstance(agent_cfg, dict) else None
    if isinstance(cfg_val, bool):
        return cfg_val
    if isinstance(cfg_val, str):
        token = cfg_val.strip().lower()
        if token in {"1", "true", "yes", "on", "strict"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        if token == "auto":
            return not _session_is_messaging_surface()
    # Missing or unrecognized value -> surface-aware "auto" default.
    return not _session_is_messaging_surface()


def verify_on_stop_strict(config: dict[str, Any] | None = None) -> bool:
    """Return whether the opt-in strict verify-on-stop hard gate is active.

    CODING-HARNESS-REVIEW-2026-07-16 §3.1: strict mode turns the soft 2-nudge
    gate into a hard block — after code edits, turn completion is refused
    until fresh passing verification evidence exists, up to
    ``STRICT_VERIFY_MAX_ATTEMPTS`` continuations, then released with a loud
    warning banner. Precedence mirrors ``verify_on_stop_enabled`` exactly so
    the two never disagree about who decides: an explicit
    ``HERMES_VERIFY_ON_STOP`` env var wins (only the literal ``strict`` token
    selects strict; any other value — including plain ``1``/``true`` — forces
    non-strict even if the config says strict, so an operator can soften a
    machine-wide strict config per-session). Otherwise only the literal
    config value ``agent.verify_on_stop: "strict"`` opts in. Every default
    (``"auto"``, bools, missing, unrecognized) is non-strict, preserving
    current behavior exactly.
    """
    env = os.environ.get("HERMES_VERIFY_ON_STOP")
    if env is not None:
        return env.strip().lower() == "strict"
    if config is None:
        try:
            from hermes_cli.config import load_config

            config = load_config()
        except Exception:
            config = {}
    agent_cfg = (config or {}).get("agent") if isinstance(config, dict) else None
    cfg_val = agent_cfg.get("verify_on_stop") if isinstance(agent_cfg, dict) else None
    return isinstance(cfg_val, str) and cfg_val.strip().lower() == "strict"


def _candidate_cwds(paths: Iterable[str]) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        try:
            path = Path(raw).expanduser()
            candidate = path if path.is_dir() else path.parent
            resolved = str(candidate.resolve())
        except Exception:
            continue
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(Path(resolved))
    return candidates


def _verification_snapshot(
    *,
    session_id: str | None,
    changed_paths: list[str],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return ``(status, facts)`` for the first edited workspace needing proof."""
    try:
        from agent.coding_context import project_facts_for
        from agent.verification_evidence import verification_status
    except Exception:
        return None

    first_snapshot: tuple[dict[str, Any], dict[str, Any]] | None = None
    for cwd in _candidate_cwds(changed_paths):
        facts = project_facts_for(cwd)
        if not facts:
            continue
        status = verification_status(session_id=session_id, cwd=cwd)
        snapshot = (status, facts)
        if first_snapshot is None:
            first_snapshot = snapshot
        if str(status.get("status") or "unverified") != "passed":
            return snapshot
    return first_snapshot


def _format_changed_paths(paths: list[str]) -> str:
    shown = paths[:_MAX_CHANGED_PATHS_IN_NUDGE]
    lines = [f"- `{path}`" for path in shown]
    remaining = len(paths) - len(shown)
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")
    return "\n".join(lines)


def _status_detail(status: dict[str, Any]) -> str:
    state = str(status.get("status") or "unverified")
    evidence = status.get("evidence") if isinstance(status.get("evidence"), dict) else None
    if not evidence:
        return state

    command = evidence.get("canonical_command") or evidence.get("command")
    summary = str(evidence.get("output_summary") or "").strip()
    parts = [state]
    if command:
        parts.append(f"last command `{command}`")
    if summary:
        max_summary = 1200
        if len(summary) > max_summary:
            summary = summary[:max_summary].rstrip() + "\n... [truncated]"
        parts.append(f"last output:\n{summary}")
    return "\n".join(parts)


def build_verify_on_stop_nudge(
    *,
    session_id: str | None,
    changed_paths: Iterable[str],
    attempts: int = 0,
    max_attempts: int = 2,
    strict: bool = False,
) -> str | None:
    """Return a synthetic follow-up when edited code lacks fresh verification.

    ``strict`` (CODING-HARNESS-REVIEW-2026-07-16 §3.1) only changes the
    message wording: the strict header states that the answer was NOT accepted
    and that asserting a blocker does not release the gate, with an explicit
    attempt counter so the model can see the loop is bounded. The caller is
    responsible for widening ``max_attempts`` to ``STRICT_VERIFY_MAX_ATTEMPTS``
    in strict mode — keeping the cap a parameter preserves the default 2-nudge
    behavior byte-for-byte when strict is off.
    """
    # Drop documentation/prose paths (markdown, skills, README, LICENSE, ...) —
    # they carry no verifiable behavior, so a turn that touched only those has
    # nothing to verify and must not nudge.
    paths = sorted({str(p) for p in _filter_verifiable_paths(changed_paths)})
    if not paths or attempts >= max_attempts:
        return None

    snapshot = _verification_snapshot(session_id=session_id, changed_paths=paths)
    if snapshot is None:
        return None
    status, facts = snapshot

    verify_commands = [
        str(cmd).strip()
        for cmd in (facts.get("verifyCommands") or [])
        if str(cmd).strip()
    ]

    state = str(status.get("status") or "unverified")
    if state == "passed":
        return None

    # Optional shipped coding guidance, only paid when this evidence gate fires.
    try:
        from agent.verify_hooks import coding_verify_guidance

        guidance = coding_verify_guidance()
    except Exception:
        guidance = None
    addendum = f"\n\n{guidance}" if guidance else ""

    if verify_commands:
        command_instruction = (
            "Run the relevant verification command now ("
            + ", ".join(f"`{cmd}`" for cmd in verify_commands[:3])
            + (", ..." if len(verify_commands) > 3 else "")
            + "), read any failure, repair the code, and summarize what passed."
        )
    else:
        temp_dir = os.path.realpath(tempfile.gettempdir())
        command_instruction = (
            "No canonical test/lint/build command was detected. Create a focused "
            f"temporary verification script under `{temp_dir}` using an OS-safe "
            "`tempfile` path with a `hermes-verify-` filename prefix, run it "
            "against the changed behavior, clean it up when possible, and "
            "summarize it explicitly as ad-hoc verification rather than suite "
            "green."
        )

    if strict:
        # Strict header: unlike the soft nudge, narrating a blocker does not
        # release this gate — only fresh passing evidence (or the caller-side
        # attempt cap) does. Say so explicitly, with the attempt counter, so
        # the model spends its turns running checks rather than negotiating.
        header = (
            "[System: STRICT verify-on-stop is enabled. You edited code in "
            "this turn, but the workspace does not have fresh passing "
            "verification evidence, so your answer was NOT accepted (attempt "
            f"{attempts + 1} of {max_attempts}). This message will repeat "
            "until passing verification evidence exists; asserting a blocker "
            "does not release the gate."
        )
        closing = (
            f"{command_instruction} If a check fails, read the failure and "
            "repair the code before finishing — do not claim completion "
            "without a fresh passing run."
        )
    else:
        header = (
            "[System: You edited code in this turn, but the workspace does not "
            "have fresh passing verification evidence yet."
        )
        closing = (
            f"{command_instruction} If verification is not possible, explain "
            "the concrete blocker instead of claiming the work is fully "
            "verified."
        )

    return (
        f"{header}\n\n"
        f"Verification status: {_status_detail(status)}\n\n"
        f"Changed paths:\n{_format_changed_paths(paths)}\n\n"
        f"{closing}"
        f"{addendum}]"
    )


def build_verify_on_stop_release_warning(
    *,
    session_id: str | None,
    changed_paths: Iterable[str],
) -> str | None:
    """Return the loud strict-mode release banner, or ``None`` when clean.

    CODING-HARNESS-REVIEW-2026-07-16 §3.1 escape hatch: when the strict gate
    exhausts ``STRICT_VERIFY_MAX_ATTEMPTS`` without fresh passing evidence,
    the turn is released anyway (a genuinely blocked model must not spin
    forever), but the final message must carry an unmissable banner so an
    unverified answer can never masquerade as a verified one. Returns ``None``
    when there is nothing to warn about: doc-only edits, no detectable code
    workspace, or evidence that is now passing (the model verified on its last
    attempt — the gate's own check already released it cleanly).
    """
    paths = sorted({str(p) for p in _filter_verifiable_paths(changed_paths)})
    if not paths:
        return None
    snapshot = _verification_snapshot(session_id=session_id, changed_paths=paths)
    if snapshot is None:
        return None
    status, _facts = snapshot
    if str(status.get("status") or "unverified") == "passed":
        return None
    return (
        "⚠️ **STRICT VERIFY-ON-STOP: RELEASED WITHOUT VERIFICATION** ⚠️\n"
        "Strict mode required fresh passing verification evidence for this "
        "turn's code edits, but none was produced after "
        f"{STRICT_VERIFY_MAX_ATTEMPTS} attempts. Verification status: "
        f"{_status_detail(status)}\n"
        "Treat every completion claim above as UNVERIFIED until you run the "
        "project's checks yourself."
    )


__all__ = [
    "STRICT_VERIFY_MAX_ATTEMPTS",
    "build_verify_on_stop_nudge",
    "build_verify_on_stop_release_warning",
    "verify_on_stop_enabled",
    "verify_on_stop_strict",
]
