# ConversoAR ↔ Hermes Integration Analysis & Health Report
**Date:** 2026-07-08  
**Status:** Operational — 3 issues found, 2 fixable now

---

## Pipeline Status

| Component | Status | Details |
|-----------|--------|---------|
| ConversoAR bridge (:7849) | ✅ Running | TTS loaded, Whisper loaded, 5 recent transcriptions |
| Voice-inbox plugin | ✅ Loaded | Watching `hermes/voice-inbox.jsonl`, 10 entries in file |
| TTS bridge (`hermes_converso_tts.py`) | ✅ Configured | `tts.provider: converso`, correct command path |
| DictadoAR → Hermes delivery | ✅ Active | Dual path: inbox + UIA paste |
| ConversoAR health monitor | ✅ Active | 30s poll, auto-restart via `DETACHED_PROCESS` |

---

## Architecture: 4 Delivery Paths

### Path 1: Voice-Inbox (PRIMARY — no window focus needed)
```
User speaks "HK-47" → DictadoAR transcribes
  → hermes_deliver.py writes to %LOCALAPPDATA%/hermes/voice-inbox.jsonl
  → voice-inbox plugin polls every 0.5s, reads new lines
  → ctx.inject_message(text, role="user") → text appears in chat
```

### Path 2: UIA Focus + Paste (FALLBACK — needs window)
```
DictadoAR agent_input.py → _focus_input_via_uia (chat_zone_y_frac=0.92)
  → finds Hermes chat input via UIA → paste + Enter
```
Runs alongside Path 1 for redundancy. Both paths deliver the same text.

### Path 3: Hermes Mic Button → DictadoAR (STT bridge)
```
User clicks mic (alt+t) → Hermes records WAV
  → hermes_converso_stt.py → POST :7849/dictate
  → DictadoAR records + transcribes → writes own inbox
  → STT bridge reads DictadoAR inbox → returns text to Hermes
```

### Path 4: Voice Bridge HTTP (standalone, port 7851)
```
POST :7851/input {"text":"..."} → UIA find Hermes window
  → ValuePattern.SetValue or clipboard paste → Enter
```

---

## Issues Found

### ISSUE 1: `stt.enabled: true` conflicts with DictadoAR
**Config:** `stt.enabled: true` with `provider: conversoar`
**Problem:** Hermes mic button (alt+t) triggers DictadoAR via STT bridge. This
conflicts with DictadoAR's own ctrl+t hotkey and wake word. Double-triggers possible.
**Fix:** Either disable `stt.enabled: false` (per skill doc) OR ensure only one
trigger path is active. The wake-word path (Path 1) is the primary — STT bridge
should be disabled unless user explicitly wants mic-button-to-DictadoAR flow.

### ISSUE 2: Voice bridge (port 7851) not running
**Status:** `curl :7851/health` → connection refused
**Impact:** Path 4 is unavailable. DictadoAR's UIA paste (Path 2) handles
delivery independently, so no data loss — but no HTTP API for external tools.
**Fix:** Launch `hermes-voice-bridge.py` as a background service, or document
it as optional and ensure Path 2 covers all cases.

### ISSUE 3: Dual delivery can duplicate messages
**Cause:** Path 1 (voice-inbox) and Path 2 (UIA paste) both deliver same text.
**Impact:** If both succeed, text appears twice in chat — once via
ctx.inject_message(), once via paste. The plugin's `inject_message` is
instant; UIA paste happens 200-500ms later after window focus gymnastics.
**Mitigation:** The redundancy is intentional (plugin mid-restart fallback).
Monitor for duplicates and consider adding a 2-second dedup window in plugin.

---

## Reliability Assessment

| Component | Reliability | Notes |
|-----------|-------------|-------|
| voice-inbox plugin | ★★★★★ | Daemon threads, error handling, file truncation recovery, proper JSON parsing |
| TTS bridge | ★★★★★ | Always writes valid MP3 (ffmpeg + hardcoded fallback), never fails Hermes |
| ConversoAR launcher | ★★★★★ | DETACHED_PROCESS + cleared PYTHONPATH, proven restart pattern |
| hermes_deliver.py | ★★★★☆ | Stdlib-only, simple JSON append, good error handling |
| hermes_converso_stt.py | ★★★☆☆ | Reads from DictadoAR inbox (may be stale/empty), 25s timeout |
| hermes-voice-bridge.py | ★★★☆☆ | UIA-dependent, requires `comtypes`, not running currently |

---

## Verified: Test the Full Pipeline

```bash
# 1. Check ConversoAR
curl -s http://127.0.0.1:7849/status  # should be running

# 2. Check voice-inbox plugin
grep "voice-inbox" ~/AppData/Local/hermes/logs/agent.log | tail -3

# 3. Test TTS
# Hermes will use converso TTS on next response — verify audio plays

# 4. Test voice input
# Say wake word "HK-47" + speak → check chat for injected text
# Or use ctrl+t to trigger DictadoAR manually

# 5. Simulate inbox delivery (no voice needed)
echo '{"text":"test from analysis"}' >> "$LOCALAPPDATA/hermes/voice-inbox.jsonl"
# Watch chat — should appear as user message in ~0.5s
```
