import type { SessionDragPayload } from '@/app/chat/composer/inline-refs'

/**
 * Registry wiring sidebar category drop zones into the pointer drag session
 * (app/chat/session-drag.ts). Categories live in the SIDEBAR, which is not a
 * pane-tree zone, so they can't ride the zones-engine snapshots — each
 * CategoryDropZone registers its element here and the session resolver
 * hit-tests the registry alongside the pane zones.
 *
 * Descendant of the pre-merge session-pointer-drag.ts fallback ("native HTML5
 * drag DEAD on this box", CRITICAL #16 era): upstream has since moved ALL
 * in-app drags off native DnD onto the shared pointer session, so only the
 * zone registry survives — the private drag loop, ghost, and phantom-click
 * suppression are the drag session's job now.
 */
export interface CategoryDropZoneHandle {
  element: HTMLElement
  /** Fires when an engaged drag releases over this zone. */
  onDrop: (payload: SessionDragPayload) => void
  /** Drives the zone's hover ring while the drag session targets it. */
  setIsOver: (over: boolean) => void
}

const zones = new Map<string, CategoryDropZoneHandle>()

export function registerCategoryDropZone(categoryId: string, handle: CategoryDropZoneHandle): () => void {
  zones.set(categoryId, handle)

  return () => {
    // Only unregister our own registration (a remount may have replaced it).
    if (zones.get(categoryId) === handle) {
      zones.delete(categoryId)
    }
  }
}

export interface CategoryZoneHit {
  categoryId: string
  handle: CategoryDropZoneHandle
}

/**
 * Live hit test — measured per resolved move, NOT snapshotted at drag start
 * like the pane zones. Deliberate deviation from the drag-session performance
 * contract: the sidebar can wheel-scroll mid-drag (pointer capture doesn't
 * swallow wheel), which would invalidate an engage-time snapshot, and the set
 * is a handful of rects measured at most once per rAF-coalesced move.
 */
export function categoryDropZoneAt(x: number, y: number): CategoryZoneHit | null {
  for (const [categoryId, handle] of zones) {
    if (!handle.element.isConnected) {
      continue
    }

    const rect = handle.element.getBoundingClientRect()

    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      return { categoryId, handle }
    }
  }

  return null
}
