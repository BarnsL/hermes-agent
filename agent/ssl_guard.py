"""Preventive SSL CA certificate checks for Hermes Agent.

This module catches broken CA bundle paths before OpenAI/httpx turns them into
opaque ``FileNotFoundError: [Errno 2] No such file or directory`` failures.
"""

from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

from agent.errors import SSLConfigurationError

logger = logging.getLogger(__name__)

_CA_BUNDLE_ENV_VARS = (
    "HERMES_CA_BUNDLE",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)

_SKIP_VALUES = {"1", "true", "yes", "on"}


def _skip_ssl_guard_enabled() -> bool:
    return os.getenv("HERMES_SKIP_SSL_GUARD", "").strip().lower() in _SKIP_VALUES


def _repair_hint() -> str:
    return (
        "Repair: python -m pip install --force-reinstall certifi openai httpx\n"
        "If you configured a custom corporate CA bundle, fix or unset the "
        "broken CA bundle environment variable."
    )


def _ssl_err(message: str) -> SSLConfigurationError:
    """Create a consistent, user-actionable SSL configuration error."""
    return SSLConfigurationError(f"{message}\n{_repair_hint()}")


def _try_vault_restore(path: Path) -> bool:
    """CRITICAL #14: Auto-restore cacert.pem from backup vault before failing.

    If Windows Defender quarantined the file, try to restore it from
    C:\\Users\\<user>\\AppData\\Local\\hermes\\.pkg-vault\\.
    Returns True if the file was successfully restored.
    """
    try:
        vault = Path(os.environ.get("HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes"))) / ".pkg-vault"
        # Map common paths to vault filenames
        vault_map = {
            "certifi/cacert.pem": "certifi_cacert.pem",
            "certifi/__init__.py": "certifi___init__.py",
        }
        rel = None
        for pkg_rel, vault_name in vault_map.items():
            if str(path).replace("\\\\", "/").endswith(pkg_rel.replace("/", "\\\\")):
                rel = vault_name
                break
            if str(path).endswith(pkg_rel.split("/")[-1]):
                rel = vault_name
                break
        if rel and (vault / rel).exists():
            import shutil
            shutil.copy2(vault / rel, path)
            logger.warning("ssl_guard: AUTO-RESTORED %s from vault [CRITICAL #14]", path)
            return True
    except Exception as exc:
        logger.debug("ssl_guard: vault restore failed: %s", exc)
    return False


def _validate_bundle_path(label: str, value: str, *, require_substantial: bool = False) -> None:
    path = Path(value).expanduser()
    if not path.exists():
        # CRITICAL #14: Try vault restore before giving up
        if _try_vault_restore(path) and path.exists():
            pass  # File restored, continue with validation
        else:
            raise _ssl_err(f"{label} points to a missing CA bundle: {value}")
    if not path.is_file():
        raise _ssl_err(f"{label} does not point to a CA bundle file: {value}")
    if require_substantial and path.stat().st_size < 1024:
        raise _ssl_err(f"{label} at {value} appears corrupted (too small)")
    try:
        ctx = ssl.create_default_context(cafile=str(path))
    except Exception as exc:
        raise _ssl_err(f"{label} CA bundle at {value} cannot be loaded: {exc}") from exc
    if not ctx.get_ca_certs():
        raise _ssl_err(f"{label} CA bundle at {value} did not load any certificates")


def verify_ca_bundle() -> None:
    """Verify configured and bundled CA certificates are present and loadable.

    Raises:
        SSLConfigurationError: If an explicit CA-bundle environment variable
            points at a bad path, or if certifi's bundled ``cacert.pem`` is
            missing/corrupt.
    """
    if _skip_ssl_guard_enabled():
        logger.debug("SSL CA bundle guard skipped via HERMES_SKIP_SSL_GUARD")
        return

    # --- CRITICAL ISSUE #14: certifi CA BUNDLE GUTTING (Defender) -------
    # This function is the FIRST line of defense against the Defender gutting
    # pattern. When Windows Defender quarantines cacert.pem (the CA bundle
    # that certifi uses for TLS verification), _validate_bundle_path at line 48
    # raises SSLConfigurationError BEFORE the OpenAI client creation fails
    # with an opaque FileNotFoundError.
    #
    # Root cause: Windows Defender quarantines .pem files as suspicious.
    # See also: #1 (yaml/__init__.py), #11 (numpy/__init__.py), #13 (Defender).
    # Fix: pip install --force-reinstall certifi==2026.5.20 + add .pem exclusion.
    # -----------------------------------------------------------------------

    for env_var in _CA_BUNDLE_ENV_VARS:
        value = os.getenv(env_var)
        if value:
            _validate_bundle_path(env_var, value)

    try:
        import certifi
    except Exception as exc:
        raise _ssl_err(f"certifi is not importable: {exc}") from exc

    ca_bundle = str(certifi.where())
    _validate_bundle_path("certifi", ca_bundle, require_substantial=True)


def verify_ca_bundle_with_fallback() -> None:
    """Backward-compatible wrapper for older call sites.

    The old PR name mentioned a platform fallback, but allowing startup with a
    broken certifi bundle still leaves httpx/OpenAI and requests call sites
    failing later. Keep the wrapper name but enforce the same check.
    """
    verify_ca_bundle()
