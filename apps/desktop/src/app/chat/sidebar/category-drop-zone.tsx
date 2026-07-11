import { useEffect, useRef, useState } from 'react'
import type * as React from 'react'

import { dragHasSession, readSessionDrag } from '@/app/chat/composer/inline-refs'
import { cn } from '@/lib/utils'

import { registerPointerDropZone } from './session-pointer-drag'

/**
 * Droppable wrapper for session categories.
 * Sessions are draggable via session-row.tsx (native HTML5 drag carrying
 * HERMES_SESSION_MIME); this component accepts those drags and highlights
 * while a session hovers over it.
 *
 * ─── CRITICAL ISSUE #16 (2026-07-09): CATEGORY DROP NEVER FIRED ─────────────
 * PROBLEM:
 *   Dragging a session onto a category did nothing — no highlight, no drop.
 * ROOT CAUSE (two independent faults):
 *   1. The drag source set effectAllowed='copy' while this zone set
 *      dropEffect='move'; Chromium cancels drops whose dropEffect isn't
 *      permitted by effectAllowed, so the drop event never fired. Fixed at the
 *      source: writeSessionDrag now uses 'copyMove' (inline-refs.ts).
 *   2. The hover highlight used dnd-kit's useDroppable/isOver, but dnd-kit
 *      only tracks dnd-kit drags (pointer-sensor reorders) — it is blind to
 *      native HTML5 drags, and there is no DndContext wiring sidebar rows to
 *      these zones. The highlight was dead code, so even the mechanism that
 *      DID exist gave zero feedback. Replaced with native dragenter/leave
 *      tracking (depth-counted, since those events re-fire on every child
 *      boundary).
 * See CRITICAL-ISSUES.md #16.
 * ─────────────────────────────────────────────────────────────────────────────
 */
export function CategoryDropZone({
  categoryId,
  children,
  className,
  onDropSession,
}: {
  categoryId: string
  children: React.ReactNode
  className?: string
  onDropSession: (sessionId: string, categoryId: string) => void
}) {
  const [isOver, setIsOver] = useState(false)
  // dragenter/dragleave fire for every descendant crossed; a depth counter is
  // the standard way to know when the pointer has truly left the zone.
  const depth = useRef(0)
  const zoneRef = useRef<HTMLDivElement | null>(null)

  // Also accept the pointer-events fallback drag (CRITICAL #16b): on machines
  // where the OS drag loop dies instantly (remote/virtual input drivers), the
  // HTML5 handlers below never fire. The registry drives the SAME isOver
  // highlight and the same drop action.
  useEffect(() => {
    const el = zoneRef.current

    if (!el) {
      return
    }

    return registerPointerDropZone(categoryId, {
      element: el,
      onDrop: payload => onDropSession(payload.id, categoryId),
      setIsOver
    })
  }, [categoryId, onDropSession])

  const reset = () => {
    depth.current = 0
    setIsOver(false)
  }

  const handleDragEnter = (e: React.DragEvent) => {
    if (!dragHasSession(e.dataTransfer)) {
      return
    }

    e.preventDefault()
    depth.current += 1
    setIsOver(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    if (!dragHasSession(e.dataTransfer)) {
      return
    }

    depth.current = Math.max(0, depth.current - 1)

    if (depth.current === 0) {
      setIsOver(false)
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    if (dragHasSession(e.dataTransfer)) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'

      // Self-heal: if a hovered child unmounted mid-drag (e.g. a background
      // refresh removed a row), its compensating dragleave fired on the
      // detached node and never bubbled here, leaving the depth counter and
      // highlight stuck. dragover fires continuously while hovering, so this
      // is the reliable ground truth.
      if (depth.current === 0) {
        depth.current = 1
        setIsOver(true)
      }
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    if (!dragHasSession(e.dataTransfer)) {
      return
    }

    e.preventDefault()
    reset()

    const payload = readSessionDrag(e.dataTransfer)

    if (payload?.id) {
      onDropSession(payload.id, categoryId)
    }
  }

  return (
    <div
      className={cn(
        'rounded-md transition-colors duration-150',
        isOver && 'bg-(--ui-control-active-background) ring-1 ring-(--ui-stroke-primary)',
        className
      )}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      ref={zoneRef}
    >
      {children}
    </div>
  )
}
