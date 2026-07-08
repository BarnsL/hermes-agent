# Hermes Restart — Root Cause Analysis & Fix

## The Problem
Every restart method attempted from within Hermes failed:
- `cmd /c start` → process attaches to MSYS2 console, dies with Hermes
- PowerShell background jobs → same console attachment
- Bash `&` background → child of pty, pty dies with Hermes
- Task Scheduler → too slow, can't coordinate with kill timing
- Cronjob spawning → creates second instance, doesn't replace killed one

## Root Cause
**Windows process tree mechanics.** Hermes.exe creates a console/pty. When `taskkill /f /im Hermes.exe` runs, Windows terminates Hermes.exe AND its entire process tree — including the pty, the bash shell, and any child processes of that shell. Any "background" or "detached" process spawned from within that shell is still IN the process tree and dies.

## The Fix: Two-Layer DETACHED_PROCESS

The solution uses Windows `DETACHED_PROCESS` (0x8) + `CREATE_NEW_PROCESS_GROUP` (0x200) flags at TWO layers:

### Layer 1: Orchestrator (`hermes_restart.py`)
1. Spawns a LAUNCHER Python process with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
2. The launcher is an inline Python script that sleeps 3 seconds, then spawns Hermes.exe
3. Because it's detached, the launcher is NOT in Hermes' process tree — it survives
4. Orchestrator then kills Hermes.exe via `taskkill /f`

### Layer 2: Launcher (inline Python code)
1. Sleeps 3 seconds (Hermes is being killed during this time)
2. Spawns Hermes.exe with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
3. Hermes.exe runs in its OWN process group, completely independent
4. Logs result to `%LOCALAPPDATA%/hermes/logs/restart_launcher.log`

### Why This Works
- `DETACHED_PROCESS`: The child runs in a new console, not attached to the parent's console. When the parent dies, the console is NOT destroyed.
- `CREATE_NEW_PROCESS_GROUP`: The child is the root of a NEW process group. Windows termination signals target process groups, so the child is immune to signals sent to the parent's group.

## Proven Working
- **Tested:** 2026-07-08 02:11 UTC
- **Result:** Hermes restarted successfully (new backend on port 63464)
- **Log:** `restart_launcher.log` — "Launched pid=21040"
- **Restart state:** tracked in `hermes_restart_state.py` (attempt #2, success)

## Files
| File | Purpose |
|---|---|
| `scripts/hermes_restart.py` | Canonical restart orchestrator — spawn launcher, kill Hermes, verify |
| `scripts/hermes_launcher.py` | Standalone launcher (simpler, for cronjob use) |
| `scripts/hermes_restart_state.py` | State tracking (pending/complete/failed, attempt counting) |
| `hermes_launcher.cmd` | Batch wrapper → delegates to Python launcher |

## The ONLY Manual Alternative
Close Hermes, then reopen from Start Menu or run:
```
hermes desktop --skip-build
```
