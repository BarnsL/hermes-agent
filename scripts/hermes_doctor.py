"""hermes_doctor.py — stdlib-only pre-flight health check / auto-repair.

RECURRING ISSUES R-1/R-2/R-5 (see RECURRING-ISSUES-AND-GAMEPLAN-2026-07-09.md
in the Hermes home dir) and systemic weakness S1/S2 (ROOT-CAUSE-ANALYSIS.md):

  * S1 — bootstrapping deadlock: `hermes update` imports yaml/openai/pydantic
    at module load, so a gutted venv kills the very tool that repairs it.
    THIS file therefore imports NOTHING outside the Python stdlib. Every
    third-party check runs in a CHILD process, so a broken package can never
    take the doctor down with it.
  * S2 — no pre-flight: Hermes used to discover missing packages only by
    crashing (PyYAML #1, numpy #11, certifi #14, typing_extensions x4).
    The doctor turns those hard crashes into a pre-boot report/repair.
  * R-5/R-6 — config corruption silently produced an empty model, which
    fell back to the Claude subscription and burned its usage limit. The
    doctor validates config.yaml BEFORE the gateway starts and can restore
    the last-good copy (written by hermes_cli/config.py on every successful
    load — see the LAST-GOOD CONFIG annotation there).

Usage:
    python scripts/hermes_doctor.py            # check only, exit 0/1
    python scripts/hermes_doctor.py --repair   # check + auto-repair
    python scripts/hermes_doctor.py --json     # machine-readable output

Wire-up: gateway-service/Hermes_Gateway.cmd runs `--repair` before launching
the gateway, and scripts/safe-update.ps1 runs it after every update.
Exit codes: 0 = healthy (or repaired), 1 = problems remain, 2 = doctor error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", r"C:\Users\Burgboy\AppData\Local\hermes"))
REPO = HERMES_HOME / "hermes-agent"
VENV_PY = REPO / "venv" / "Scripts" / "python.exe"
CONFIG = HERMES_HOME / "config.yaml"
CONFIG_GOOD = HERMES_HOME / "config.yaml.good"
UPDATE_LOCK = HERMES_HOME / ".hermes-update-in-progress"
ASAR = REPO / "apps" / "desktop" / "release" / "win-unpacked" / "resources" / "app.asar"

# Never flash a console window if we're launched from pythonw / a hidden host
# (user etiquette: no visible windows — the desktop user is often gaming).
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _child(code: str, timeout: int = 60) -> tuple[bool, str]:
    """Run a snippet in a child interpreter so import crashes can't kill us."""
    py = str(VENV_PY if VENV_PY.exists() else sys.executable)
    try:
        p = subprocess.run(
            [py, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, out.strip()[-400:]
    except Exception as e:  # noqa: BLE001 — doctor must never crash
        return False, f"child failed to run: {e}"


class Check:
    def __init__(self, name: str, ok: bool, detail: str = "", repair=None):
        self.name, self.ok, self.detail, self.repair = name, ok, detail, repair


def check_stale_update_lock() -> Check:
    """CRITICAL #2: a dead-PID .hermes-update-in-progress blocks boot forever."""
    if not UPDATE_LOCK.exists():
        return Check("update-lock", True, "no lock present")
    try:
        raw = UPDATE_LOCK.read_text(errors="replace").strip()
        pid = int("".join(ch for ch in raw if ch.isdigit()) or "0")
    except OSError:
        pid = 0
    age_min = (time.time() - UPDATE_LOCK.stat().st_mtime) / 60
    alive = False
    if pid:
        # tasklist is stdlib-reachable and needs no elevation
        ok, out = True, ""
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=15,
                creationflags=CREATE_NO_WINDOW,
            ).stdout
        except Exception:  # noqa: BLE001
            ok = False
        alive = ok and str(pid) in (out or "")
    if alive and age_min < 30:
        return Check("update-lock", True, f"update genuinely running (pid={pid}, {age_min:.0f}m)")

    def repair():
        UPDATE_LOCK.unlink(missing_ok=True)
        return f"removed stale lock (pid={pid or '?'} dead, age={age_min:.0f}m)"
    return Check("update-lock", False,
                 f"STALE lock: pid={pid or '?'} not running / age={age_min:.0f}m — blocks desktop boot",
                 repair)


def check_packages() -> list[Check]:
    """R-2: the Defender-gutting family. File-existence first, then imports."""
    probes = [
        ("certifi/cacert.pem",
         "import certifi,os,sys; p=certifi.where(); sys.exit(0 if os.path.getsize(p)>200000 else 1)"),
        ("yaml.SafeDumper",
         "import yaml,sys; sys.exit(0 if hasattr(yaml,'SafeDumper') else 1)"),
        ("numpy.ndarray",
         "import numpy,sys; sys.exit(0 if hasattr(numpy,'ndarray') else 1)"),
        ("httpx.TransportError",
         "import httpx,sys; sys.exit(0 if hasattr(httpx,'TransportError') else 1)"),
        ("typing_extensions",
         "import typing_extensions"),
        ("openai import",
         "import openai"),
        ("fastapi+pydantic import",
         "import fastapi, pydantic"),
    ]
    checks = []
    for name, code in probes:
        ok, detail = _child(code)

        def repair(_name=name):
            # First line of defense: the hash-verified vault restore.
            ok2, out2 = _child(
                "from agent.pkg_guard import verify_package_health as v; "
                "import sys; sys.exit(0 if v(repair=True) else 1)",
                timeout=120,
            )
            return f"pkg_guard restore {'succeeded' if ok2 else 'FAILED: ' + out2}"
        checks.append(Check(f"pkg:{name}", ok, "" if ok else detail, None if ok else repair))
    return checks


def check_config() -> Check:
    """R-5: config.yaml corruption → empty model → R-6 subscription burn."""
    if not CONFIG.exists():
        return Check("config", False, "config.yaml MISSING")
    code = (
        "import sys, yaml\n"
        f"d = yaml.safe_load(open(r'{CONFIG}', encoding='utf-8'))\n"
        "assert isinstance(d, dict), 'config parsed to non-dict'\n"
        "m = d.get('model') or {}\n"
        "assert m.get('provider'), 'model.provider missing'\n"
        "assert m.get('default'), 'model.default missing'\n"
        "print(m.get('provider'), m.get('default'))\n"
    )
    ok, detail = _child(code)
    if ok:
        return Check("config", True, f"model = {detail}")

    def repair():
        if not CONFIG_GOOD.exists():
            return "no config.yaml.good available — restore a config.yaml.corrupt.*.bak by hand"
        ts = time.strftime("%Y%m%d-%H%M%S")
        bad = CONFIG.with_name(f"config.yaml.corrupt.{ts}.bak")
        shutil.copy2(CONFIG, bad)
        shutil.copy2(CONFIG_GOOD, CONFIG)
        return f"restored last-good config (corrupt copy saved to {bad.name})"
    return Check("config", False, f"config invalid: {detail}", repair)


def check_asar_regex() -> Check:
    """CRITICAL #6/#9: stale ready-token regex in packaged app.asar → boot loop."""
    if not ASAR.exists():
        return Check("asar-regex", True, "app.asar not present (dev layout?) — skipped")
    try:
        data = ASAR.read_bytes()
        ok = b"BACKEND|DASHBOARD" in data or b"(?:BACKEND|DASHBOARD)" in data
        return Check("asar-regex", ok,
                     "packaged regex accepts both ready tokens" if ok
                     else "STALE asar: only legacy DASHBOARD token — desktop will boot-loop. "
                          "Repack from apps/desktop/electron/backend-ready.cjs (preserve CRLF!)")
    except OSError as e:
        return Check("asar-regex", False, f"cannot read asar: {e}")


def check_vault() -> Check:
    """S-7: vault must exist and carry hashes; pkg_guard verifies them on restore."""
    vault = HERMES_HOME / ".pkg-vault"
    man = vault / "manifest.json"
    if not vault.is_dir() or not man.exists():
        return Check("pkg-vault", False, "vault or manifest.json missing — recreate per CRITICAL #14")
    try:
        raw = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return Check("pkg-vault", False, f"manifest unreadable: {e}")
    pkgs = raw.get("packages", raw) if isinstance(raw, dict) else {}
    hashed = sum(1 for v in pkgs.values() if isinstance(v, dict) and v.get("sha256"))
    files = [p for p in vault.iterdir()
             if p.is_file() and p.name != "manifest.json" and not p.name.endswith(".bak")]
    ok = hashed >= len(files)
    return Check("pkg-vault", ok,
                 f"{len(files)} backups, {hashed} hashed"
                 + ("" if ok else " — run scripts/refresh-vault.py (unhashed files can't be restored)"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes stdlib-only pre-flight doctor")
    ap.add_argument("--repair", action="store_true", help="attempt auto-repair of failures")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    checks: list[Check] = [check_stale_update_lock()]
    checks += check_packages()
    checks += [check_config(), check_asar_regex(), check_vault()]

    problems = [c for c in checks if not c.ok]
    repaired: dict[str, str] = {}
    if args.repair:
        for c in problems:
            if c.repair:
                try:
                    repaired[c.name] = c.repair()
                except Exception as e:  # noqa: BLE001
                    repaired[c.name] = f"repair crashed: {e}"
        # Re-run the failed checks once after repair
        if repaired:
            rerun: list[Check] = [check_stale_update_lock()]
            rerun += check_packages()
            rerun += [check_config(), check_asar_regex(), check_vault()]
            checks = rerun
            problems = [c for c in checks if not c.ok]

    if args.json:
        print(json.dumps({
            "healthy": not problems,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
            "repaired": repaired,
        }, indent=2))
    else:
        for c in checks:
            print(f"  [{'OK' if c.ok else 'FAIL'}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
        for name, msg in repaired.items():
            print(f"  [REPAIR] {name}: {msg}")
        print(f"hermes_doctor: {'HEALTHY' if not problems else f'{len(problems)} PROBLEM(S) REMAIN'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — a doctor that crashes is useless
        print(f"hermes_doctor: internal error: {exc}", file=sys.stderr)
        sys.exit(2)
