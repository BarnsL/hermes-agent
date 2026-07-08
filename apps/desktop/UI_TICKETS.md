# Desktop UI Tickets

## TKT-001 — Plus button crashes UI
**Status:** ✅ Fixed (a7da63c9f)
**Root cause:** `useState` called inside `.map()` callback — React hooks order violation.
**Fix:** Extracted `CategorySection` → `SessionCategoriesSection`.

## TKT-002 — Category section spacing
**Status:** ✅ Fixed (098cf88bd)

## TKT-003 — Blank sessions on rapid switch
**Status:** ✅ Fixed
**Root cause:** `use-session-state-cache.ts` effect cleared `$messages` on every `activeSessionId` change, nuking prefetched transcripts.
**Fix:** Guard: only clear when replacing known view (`viewSessionIdRef.current !== null`). Reset on null transitions.
**File:** `src/app/session/hooks/use-session-state-cache.ts`

## TKT-004 — Kebab-vertical icon consistency
**Status:** ✅ Fixed
**Fix:** `ellipsis` → `kebab-vertical` in `session-categories-section.tsx`, `env-var-actions-menu.tsx`, `assistant-message.tsx`.
**Kept:** `load-more-row.tsx`, `workspace-header.tsx`, `pagination.tsx` (non-menu affordances).

## TKT-005 — Vertical text overlap in categories
**Status:** ✅ Fixed
**Root cause:** `CategorySection` had `min-h-0` — flex squish to zero height.
**Fix:** Removed `min-h-0`, added `pb-2`.
**File:** `src/app/chat/sidebar/session-categories-section.tsx`

## TKT-006 — Tray restart fails silently
**Status:** ✅ Fixed
**Root cause:** `resetHermesConnection()` didn't clear `bootstrapFailure`.
**Fix:** Added `bootstrapFailure = null`.
**File:** `electron/main.cjs`

## TKT-007 — Discord/cron layout
**Status:** ✅ Fixed
**Fix:** Wrapped messaging + cron in `shrink-0` div with `border-t` + `pt-1.5` inside scroll container.
**File:** `src/app/chat/sidebar/index.tsx`

## TKT-008 — Scroll wheel locks at bottom
**Status:** ✅ Fixed
**Root cause:** `overscroll-contain` consumed wheel events at boundaries in Chromium.
**Fix:** Removed from `SCROLL_Y`.
**File:** `src/app/chat/sidebar/index.tsx`

## TKT-009 — Greenshot wrong display
**Status:** ✅ Fixed
**Fix:** `ScreenCaptureMode=FullScreen`, `FullscreenScreenCaptureMode=Primary`.
**File:** `%APPDATA%/Greenshot/Greenshot.ini`

## TKT-010 — Session bleed on rapid switch
**Status:** ✅ Fixed (revised)
**Root cause:** Warm cache resume didn't clear old messages before repainting, causing 1-frame bleed.
**Fix v1 (reverted):** Moved `setMessages([])` before `takeWarmCache()` — caused TKT-003 regression (forever-loading sessions on unstable backend).
**Fix v2:** `use-session-state-cache.ts` effect clears old messages when `viewSessionIdRef` changes to a genuinely different ID. Resets `viewSessionIdRef` on null transitions so cold resume doesn't nuke prefetched transcript.
**File:** `src/app/session/hooks/use-session-state-cache.ts`

## TKT-011 — Reliable Hermes restart
**Status:** ✅ Fixed & Verified (2026-07-08 02:11 UTC)
**Root cause:** All prior approaches failed because child processes die with Hermes pty.
**Fix:** Two-layer `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` spawning.
**Canonical script:** `skills/software-development/hermes-restart/scripts/hermes_restart.py`
**Full analysis:** `apps/desktop/ROOT_CAUSE_ANALYSIS_RESTART.md`
