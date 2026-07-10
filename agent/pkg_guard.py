#!/usr/bin/env python3
"""
Hermes Package Guardian v2 -- auto-restore for Defender-gutted packages.

FIXES from v1:
- File-based checks are PRIMARY (reliable). Import tests are SECONDARY.
- Fixed yaml/constructor test (Constructor is in yaml.constructor).
- Fixed openai test (file-only -- import has too many transitive deps).
- Added typing_extensions to checks (gutted 4th time).

CRITICAL #1/#11/#14 mitigation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import NamedTuple

import logging
logger = logging.getLogger(__name__)

_HERMES_HOME = Path(os.environ.get(
    "HERMES_HOME",
    str(Path.home() / "AppData" / "Local" / "hermes"),
))
_AGENT_DIR = _HERMES_HOME / "hermes-agent"
_SITE_PACKAGES = _AGENT_DIR / "venv" / "Lib" / "site-packages"
_VAULT = _HERMES_HOME / ".pkg-vault"
_MANIFEST = _VAULT / "manifest.json"

# ─── SECURITY AUDIT S-7 / GAME PLAN G-2: HASH-VERIFY BEFORE RESTORE ───────────
# This module auto-copies files from .pkg-vault into site-packages on EVERY
# Python startup (via sitecustomize.py). Before this change the copy was blind:
# whatever bytes sat in the vault got written into the interpreter's import
# path, no questions asked. That makes the vault a persistence/backdoor vector —
# tamper with one vault file and it reinstalls itself into every Hermes process
# forever. G-2 requires the vault to carry a SHA-256 in manifest.json and this
# code to verify it before copying, so a restore can only ever reproduce the
# exact bytes that were vaulted at a known-good moment.
#
# POLICY for a vault file with NO manifest hash (default = "refuse"):
#   Set HERMES_PKG_GUARD_ALLOW_UNHASHED=1 to fall back to the legacy size-only
#   behavior for un-manifested files (e.g. right after adding a new package to
#   the vault, before regenerating the manifest). Default refuses and tells you
#   to run refresh-vault. Restoring a HASHED file whose bytes don't match is
#   ALWAYS refused, regardless of this flag.
_ALLOW_UNHASHED = os.environ.get("HERMES_PKG_GUARD_ALLOW_UNHASHED") == "1"


def _load_manifest() -> dict:
    """Return manifest 'packages' map (site-packages-relative key -> entry).

    Never raises: a missing/corrupt manifest yields {} and the per-file policy
    (refuse unless _ALLOW_UNHASHED) then applies. Stdlib only, so this is safe
    to call before third-party packages are known-good.
    """
    try:
        raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):  # valid JSON but a list/scalar — honor "never raises"
        return {}
    pkgs = raw.get("packages", raw)
    return pkgs if isinstance(pkgs, dict) else {}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _vault_hash_ok(check: dict, vault_file: Path) -> tuple[bool, str]:
    """(ok_to_restore, reason). Enforces the S-7/G-2 hash policy."""
    manifest = _load_manifest()
    entry = manifest.get(check["file"]) or {}
    expected = (entry.get("sha256") or "").lower().strip()
    if not expected:
        if _ALLOW_UNHASHED:
            return True, "no manifest hash — allowed by HERMES_PKG_GUARD_ALLOW_UNHASHED=1"
        return False, (f"no sha256 in manifest for {check['file']} — restore refused "
                       f"(run scripts/refresh-vault.py, or set "
                       f"HERMES_PKG_GUARD_ALLOW_UNHASHED=1 to override)")
    try:
        actual = _sha256(vault_file)
    except OSError as exc:
        return False, f"cannot hash vault file: {exc}"
    if actual != expected:
        return False, (f"vault sha256 mismatch for {check['vault']} "
                       f"(expected {expected[:12]}…, got {actual[:12]}…) — restore refused")
    return True, "sha256 verified"

_HEALTH_CHECKS = [
    {"package": "certifi", "file": "certifi/cacert.pem", "vault": "certifi_cacert.pem",
     "min_size": 100_000, "issue": "#14", "import_test": "import certifi; certifi.where()"},
    {"package": "yaml", "file": "yaml/__init__.py", "vault": "yaml___init__.py",
     "min_size": 5_000, "issue": "#1", "import_test": "import yaml; yaml.SafeDumper"},
    {"package": "yaml", "file": "yaml/constructor.py", "vault": "yaml_constructor.py",
     "min_size": 10_000, "issue": "#1", "import_test": "from yaml.constructor import Constructor"},
    {"package": "numpy", "file": "numpy/__init__.py", "vault": "numpy___init__.py",
     "min_size": 5_000, "issue": "#11", "import_test": "import numpy; numpy.ndarray"},
    {"package": "typing_extensions", "file": "typing_extensions.py", "vault": "typing_extensions.py",
     "min_size": 10_000, "issue": "#14", "import_test": "import typing_extensions; typing_extensions.Literal"},
    {"package": "openai", "file": "openai/__init__.py", "vault": "openai___init__.py",
     "min_size": 1_000, "issue": "#14", "import_test": None},
    {"package": "httpx", "file": "httpx/__init__.py", "vault": "httpx___init__.py",
     "min_size": 500, "issue": "#14", "import_test": None},
]


class RepairResult(NamedTuple):
    package: str
    file: str
    status: str
    message: str
    issue: str


def _file_healthy(path: Path, min_size: int) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size >= min_size
    except OSError:
        return False


def _try_import_test(test_code: str) -> bool:
    try:
        exec(test_code, {})
        return True
    except Exception:
        return False


def _restore_from_vault(check: dict) -> RepairResult:
    pkg = check["package"]
    rel_path = check["file"]
    vault_name = check["vault"]
    issue = check.get("issue", "?")
    src_file = _SITE_PACKAGES / rel_path
    vault_file = _VAULT / vault_name

    if not vault_file.exists():
        return RepairResult(pkg, rel_path, "vault_missing",
                           f"Vault file {vault_name} not found", issue)

    # S-7/G-2: verify the vault copy against manifest.json BEFORE touching the
    # filesystem, so a refused restore has zero side effects (no mkdir, no copy).
    ok, reason = _vault_hash_ok(check, vault_file)
    if not ok:
        logger.error("pkg_guard: RESTORE REFUSED [CRITICAL %s] %s -- %s", issue, rel_path, reason)
        return RepairResult(pkg, rel_path, "hash_mismatch", reason, issue)

    src_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(vault_file, src_file)
        size = src_file.stat().st_size
        return RepairResult(pkg, rel_path, "restored",
                           f"Restored from vault ({size:,} bytes) [CRITICAL {issue}]", issue)
    except Exception as exc:
        return RepairResult(pkg, rel_path, "failed", f"Restore failed: {exc}", issue)


def _clear_import_cache(package_name: str) -> None:
    to_remove = [k for k in sys.modules if k == package_name or k.startswith(package_name + ".")]
    for key in to_remove:
        del sys.modules[key]


def verify_package_health(*, repair: bool = True, verbose: bool = False) -> list[RepairResult]:
    results: list[RepairResult] = []

    for check in _HEALTH_CHECKS:
        pkg = check["package"]
        rel_path = check["file"]
        min_size = check["min_size"]
        issue = check.get("issue", "?")
        src_file = _SITE_PACKAGES / rel_path

        file_ok = _file_healthy(src_file, min_size)

        if file_ok:
            if verbose:
                test_code = check.get("import_test")
                if test_code:
                    import_ok = _try_import_test(test_code)
                    if import_ok:
                        print(f"  [OK]     {pkg:20s} {rel_path} (import OK)")
                    else:
                        print(f"  [WARN]   {pkg:20s} {rel_path} (file OK, import warns)")
                else:
                    print(f"  [OK]     {pkg:20s} {rel_path} ({src_file.stat().st_size:,} bytes)")

            results.append(RepairResult(pkg, rel_path, "ok", "healthy", issue))
            continue

        logger.warning("pkg_guard: GUTTING DETECTED [CRITICAL %s] -- %s", issue, rel_path)

        if not repair:
            results.append(RepairResult(pkg, rel_path, "failed",
                                        "GUTTED -- repair disabled", issue))
            continue

        result = _restore_from_vault(check)
        if result.status == "restored":
            _clear_import_cache(pkg)
            logger.info("pkg_guard: AUTO-RESTORED %s [CRITICAL %s]", rel_path, issue)
            print(f"  [RESTORE] {pkg:20s} {rel_path} <- vault [CRITICAL {issue}]", file=sys.stderr)

        results.append(result)

    return results


def is_healthy() -> bool:
    results = verify_package_health(repair=False)
    return all(r.status == "ok" for r in results)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermes Package Guardian")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true", default=True)
    args = parser.parse_args()

    print("=" * 60)
    print("HERMES PACKAGE GUARDIAN")
    print("=" * 60)

    results = verify_package_health(repair=not args.check, verbose=True)

    ok = sum(1 for r in results if r.status == "ok")
    restored = sum(1 for r in results if r.status == "restored")
    failed = sum(1 for r in results if r.status in ("failed", "vault_missing", "hash_mismatch"))

    print(f"\n{'=' * 60}")
    if failed > 0:
        print(f"RESULT: {failed} package(s) need manual repair")
        for r in results:
            if r.status in ("failed", "vault_missing", "hash_mismatch"):
                print(f"  [!] {r.package}: {r.message}")
    elif restored > 0:
        print(f"RESULT: {restored} file(s) auto-restored")
    else:
        print(f"RESULT: All {ok} checks passed")

    sys.exit(1 if failed > 0 else 0)
