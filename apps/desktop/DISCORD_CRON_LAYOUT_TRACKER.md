# Discord/Cron UI Position — Issue Tracking

## Issue: Messaging & Cron Jobs overlap sessions, appear in middle, not at visual bottom of scroll area

**Severity:** High — User has reported this 4+ turns
**First reported:** 2026-07-08
**Status:** IN PROGRESS

### Attempts

| # | Approach | Result |
|---|---|---|
| 1 | Moved out of scroll, `shrink-0` bottom | User wanted INSIDE scroll |
| 2 | Moved back in scroll, no layout change | Still middle/overlapping |
| 3 | Added `shrink-0 border-t pt-1.5` wrapper | Visual separation added, but still middle |
| 4 | Added `flex-1` to recents `rootClassName` | FILE REVERTED — fix lost on next build |
| 5 | Re-added `flex-1` to recents `min-h-32 flex-1 p-0` | PENDING VERIFICATION |

### Root Cause Analysis

The scroll container (`flex min-h-0 flex-1 flex-col overflow-y-auto`) contains:
1. Pinned (`shrink-0`) — stays at top
2. Categories — natural height
3. Recents (`rootClassName`) — previously `min-h-32 p-0` (no flex grow)
4. Messaging/Cron wrapper (`shrink-0 border-t pt-1.5`)

Without `flex-1` on recents, all items stack from top. Messaging/cron appears right after recents ends. If recents is short, they appear in the middle of the visual space.

### Current Fix (Attempt #5)
Added `flex-1` to recents's `rootClassName`: `min-h-32 flex-1 p-0`

This makes recents expand to fill all remaining vertical space, pushing messaging/cron to the visual bottom.

### Verification Checklist
- [ ] `flex-1` persists in built bundle
- [ ] Messaging/cron at visual bottom (not middle)
- [ ] No overlap with recents or categories
- [ ] Scroll works — sessions scroll, messaging/cron visible at bottom
- [ ] Border separator renders correctly

### Next Steps If Still Failing
1. Verify `flex-1` is actually in the built JS bundle (search for `min-h-32` in dist)
2. Try `mt-auto` on wrapper instead of `flex-1` on recents
3. Try CSS Grid layout
4. Split into two containers: scroll area + fixed messaging/cron bottom bar
