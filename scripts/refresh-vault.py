"""refresh-vault.py — rebuild .pkg-vault/manifest.json with SHA-256 for every
vault file, and (optionally) refresh the vault copies from the live venv.

SECURITY AUDIT S-7 / GAME PLAN G-2: agent/pkg_guard.py now REFUSES to restore a
vault file that has no matching SHA-256 in manifest.json (or whose bytes don't
match). Run this after you change/upgrade any vaulted package so the manifest
tracks the new known-good bytes. Stdlib only.

Usage:
    python scripts/refresh-vault.py            # re-hash existing vault files
    python scripts/refresh-vault.py --resync   # first copy current good files
                                               # from the venv into the vault,
                                               # THEN hash them
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", r"C:\Users\Burgboy\AppData\Local\hermes"))
VAULT = HERMES_HOME / ".pkg-vault"
MANIFEST = VAULT / "manifest.json"
SITE = HERMES_HOME / "hermes-agent" / "venv" / "Lib" / "site-packages"

# vault filename  ->  site-packages-relative path (manifest key, forward slashes)
VAULT_MAP = {
    "certifi_cacert.pem":        "certifi/cacert.pem",
    "certifi___init__.py":       "certifi/__init__.py",
    "yaml___init__.py":          "yaml/__init__.py",
    "yaml_constructor.py":       "yaml/constructor.py",
    "numpy___init__.py":         "numpy/__init__.py",
    "numpy__core___init__.py":   "numpy/_core/__init__.py",
    "numpy_version.py":          "numpy/version.py",
    "openai___init__.py":        "openai/__init__.py",
    "httpx___init__.py":         "httpx/__init__.py",
    "pydantic___init__.py":      "pydantic/__init__.py",
    "typing_extensions.py":      "typing_extensions.py",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resync", action="store_true",
                    help="copy current good files from the venv into the vault before hashing")
    args = ap.parse_args()

    if not VAULT.is_dir():
        print(f"[FAIL] vault not found: {VAULT}")
        return 1

    packages: dict[str, dict] = {}
    for vault_name, rel in VAULT_MAP.items():
        vault_file = VAULT / vault_name
        if args.resync:
            src = SITE / rel
            if src.exists():
                shutil.copy2(src, vault_file)
                print(f"  [resync] {rel} -> {vault_name}")
            else:
                print(f"  [skip]   {rel} not in venv")
        if not vault_file.exists():
            print(f"  [MISS]   vault file absent: {vault_name}")
            continue
        digest = sha256(vault_file)
        packages[rel] = {
            "vault_name": vault_name,
            "sha256": digest,
            "size": vault_file.stat().st_size,
            "status": "ok",
        }
        print(f"  [hash]   {vault_name:28s} {digest[:16]}…")

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "SHA-256 of each .pkg-vault file. pkg_guard refuses to restore "
                "a file whose bytes do not match its entry here (audit S-7/G-2).",
        "packages": packages,
    }
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, MANIFEST)
    print(f"[OK] wrote {MANIFEST} with {len(packages)} hashed entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
