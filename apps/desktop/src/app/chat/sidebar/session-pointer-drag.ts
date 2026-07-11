import type * as React from 'react'

import type { SessionDragPayload } from '@/app/chat/composer/inline-refs'

/**
 * Pointer-events fallback for dragging sessions into categories.
 *
 * ─── CRITICAL ISSUE #16b (2026-07-10): NATIVE HTML5 DRAG DIES ON THIS BOX ───
 * Diagnostics proved dragstart fires (payload written, handler returns), then
 * the drag silently dies: no dragover anywhere, no dragend, no drop — ever.
 * That signature means Windows' OLE drag loop (::DoDragDrop) aborts the
 * instant it starts. Chromium hands the drag to the OS after dragstart; when
 * the OS loop refuses (virtual/remote input drivers — Parsec-style virtual
 * HID is a known trigger — or other input hooks), the web page simply never
 * hears about the drag again. No web-side fix can revive it.
 *
 * SOLUTION: this module implements the same interaction with raw pointer
 * events, which never leave the renderer. Coexistence with native DnD is
 * self-arbitrating:
 *   - Healthy machine: the native drag loop engages a few px into the
 *     gesture and the OS STOPS delivering pointermove — this tracker never
 *     reaches its threshold and stays dormant; the HTML5 path (drop zones in
 *     category-drop-zone.tsx) handles everything.
 *   - Broken machine (this one): the native loop dies immediately, pointer
 *     events keep flowing (the button is still held), the tracker crosses
 *     its threshold and takes over: portal-free DOM ghost, zone hit-testing
 *     via the registry below, same isOver highlight, same onDropSession.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * Zones register themselves (element + highlight setter + drop callback);
 * rows call `startSessionPointerTracking` from pointerdown on the row body.
 */

interface PointerDropZone {
  element: HTMLElement
  setIsOver: (over: boolean) => void
  onDrop: (payload: SessionDragPayload) => void
}

const zones = new Map<string, PointerDropZone>()

export function registerPointerDropZone(categoryId: string, zone: PointerDropZone): () => void {
  zones.set(categoryId, zone)

  return () => {
    // Only unregister our own registration (a remount may have replaced it).
    if (zones.get(categoryId) === zone) {
      zones.delete(categoryId)
    }
  }
}

// Movement (px) before the fallback engages. Must comfortably exceed the
// distance at which a WORKING native drag freezes pointer events (~4px), so
// on healthy machines this tracker never fires.
const DRAG_THRESHOLD_PX = 6
const GHOST_OFFSET_X = 14
const GHOST_OFFSET_Y = 10

// True from the moment a fallback drag crosses the threshold until the next
// macrotask after it ends — lets row onClick suppress the phantom click that
// follows pointerup.
let dragJustHappened = false

export function consumePointerDragClick(): boolean {
  return dragJustHappened
}

let active: null | {
  payload: SessionDragPayload
  startX: number
  startY: number
  dragging: boolean
  ghost: HTMLDivElement | null
  hoverZoneId: string | null
  cleanup: () => void
} = null

function makeGhost(title: string): HTMLDivElement {
  const ghost = document.createElement('div')
  ghost.textContent = title || 'session'
  // Inline styles on purpose: this node lives outside React and must not
  // depend on Tailwind class generation. Theme comes from the CSS vars.
  Object.assign(ghost.style, {
    position: 'fixed',
    left: '0px',
    top: '0px',
    zIndex: '2147483647',
    pointerEvents: 'none',
    maxWidth: '240px',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    padding: '3px 10px',
    borderRadius: '6px',
    fontSize: '12px',
    lineHeight: '1.4',
    color: 'var(--foreground, #ddd)',
    background: 'var(--ui-sidebar-surface-background, #222)',
    border: '1px solid var(--ui-stroke-tertiary, #444)',
    boxShadow: '0 4px 14px rgba(0,0,0,0.35)',
    opacity: '0.95'
  } satisfies Partial<CSSStyleDeclaration>)
  document.body.appendChild(ghost)

  return ghost
}

function zoneAt(x: number, y: number): null | { id: string; zone: PointerDropZone } {
  for (const [id, zone] of zones) {
    if (!zone.element.isConnected) {
      continue
    }

    const rect = zone.element.getBoundingClientRect()

    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      return { id, zone }
    }
  }

  return null
}

function setHover(next: null | string) {
  if (!active || active.hoverZoneId === next) {
    return
  }

  if (active.hoverZoneId) {
    zones.get(active.hoverZoneId)?.setIsOver(false)
  }

  if (next) {
    zones.get(next)?.setIsOver(true)
  }

  active.hoverZoneId = next
}

function endDrag(commit: boolean, x?: number, y?: number) {
  const state = active

  if (!state) {
    return
  }

  active = null
  state.cleanup()
  state.ghost?.remove()

  if (state.hoverZoneId) {
    zones.get(state.hoverZoneId)?.setIsOver(false)
  }

  if (state.dragging) {
    // Suppress the click that fires right after pointerup ends a drag.
    dragJustHappened = true
    setTimeout(() => {
      dragJustHappened = false
    }, 0)

    if (commit && x != null && y != null) {
      const hit = zoneAt(x, y)

      if (hit) {
        console.error('[dnd] pointer-fallback drop', state.payload.id, '→', hit.id)
        hit.zone.onDrop(state.payload)
      }
    }
  }
}

/**
 * Call from pointerdown on a session row body. Tracks the gesture and, if the
 * pointer travels past the threshold while the native drag loop is dead,
 * runs the fallback drag. No-ops for non-primary buttons, modified clicks
 * (shift-pin / ctrl-new-window), touch, and reorder-handle presses.
 */
export function startSessionPointerTracking(event: React.PointerEvent, payload: SessionDragPayload) {
  if (
    event.button !== 0 ||
    event.shiftKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    (event.pointerType !== 'mouse' && event.pointerType !== 'pen') ||
    (event.target as HTMLElement).closest('[data-reorder-handle]')
  ) {
    return
  }

  // One gesture at a time.
  endDrag(false)

  const onMove = (e: PointerEvent) => {
    const state = active

    if (!state) {
      return
    }

    if (!state.dragging) {
      const dx = e.clientX - state.startX
      const dy = e.clientY - state.startY

      if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) {
        return
      }

      // Native drag never engaged (its loop freezes pointermove) — take over.
      state.dragging = true
      state.ghost = makeGhost(payload.title)
      console.error('[dnd] pointer-fallback drag engaged', payload.id)
    }

    if (state.ghost) {
      state.ghost.style.transform = `translate(${e.clientX + GHOST_OFFSET_X}px, ${e.clientY + GHOST_OFFSET_Y}px)`
    }

    setHover(zoneAt(e.clientX, e.clientY)?.id ?? null)
  }

  const onUp = (e: PointerEvent) => endDrag(true, e.clientX, e.clientY)
  const onCancel = () => endDrag(false)

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      endDrag(false)
    }
  }

  // A WORKING native drag fires dragover continuously; if we see one, the OS
  // loop owns this gesture — stand down so the two systems never double-run.
  const onNativeDragOver = () => endDrag(false)

  window.addEventListener('pointermove', onMove, true)
  window.addEventListener('pointerup', onUp, true)
  window.addEventListener('pointercancel', onCancel, true)
  window.addEventListener('keydown', onKeyDown, true)
  window.addEventListener('dragover', onNativeDragOver, true)
  window.addEventListener('blur', onCancel)

  active = {
    payload,
    startX: event.clientX,
    startY: event.clientY,
    dragging: false,
    ghost: null,
    hoverZoneId: null,
    cleanup: () => {
      window.removeEventListener('pointermove', onMove, true)
      window.removeEventListener('pointerup', onUp, true)
      window.removeEventListener('pointercancel', onCancel, true)
      window.removeEventListener('keydown', onKeyDown, true)
      window.removeEventListener('dragover', onNativeDragOver, true)
      window.removeEventListener('blur', onCancel)
    }
  }
}
