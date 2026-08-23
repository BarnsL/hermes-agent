"""Behavior tests for the desktop smoke harness."""

import runpy
from pathlib import Path

from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_smoke_harness_uses_active_profile_home(tmp_path: Path) -> None:
    """The harness must read logs from the active Hermes profile."""
    token = set_hermes_home_override(tmp_path)
    try:
        namespace = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "scripts" / "e2e_smoke.py")
        )
    finally:
        reset_hermes_home_override(token)

    assert namespace["HERMES_HOME"] == tmp_path
    assert namespace["DESKTOP_LOG"] == tmp_path / "logs" / "desktop.log"


def test_discover_token_uses_only_an_explicit_fallback_file(
    tmp_path: Path, monkeypatch
) -> None:
    """A token file must be opt-in instead of a machine-specific default."""
    token_file = tmp_path / "serve-token.txt"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("HERMES_E2E_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "e2e_smoke.py")
    )
    discover_token = namespace["discover_token"]
    discover_token.__globals__["_find_backend_pid"] = lambda: 0

    assert discover_token() == "test-token"
