import { useEffect, useRef, useState } from 'react'
import type * as React from 'react'

import { cn } from '@/lib/utils'

import { registerCategoryDropZone } from './category-drop-zones'

/**
 * Droppable wrapper for session categories. Registers with the category zone
 * registry (category-drop-zones.ts); the sidebar session drag — the shared
 * pointer drag session in app/chat/session-drag.ts — hit-tests the registry
 * per move, drives the isOver highlight, and calls onDropSession on release.
 *
 * No native HTML5 handlers on purpose: sessions never ride native DnD anymore
 * (see session-drag.ts — native DnD is reserved for true OS boundaries like
 * Finder file drops), so dragenter/drop here would never fire.
 */
export function CategoryDropZone({
  categoryId,
  children,
  className,
  onDropSession
}: {
  categoryId: string
  children: React.ReactNode
  className?: string
  onDropSession: (sessionId: string, categoryId: string) => void
}) {
  const [isOver, setIsOver] = useState(false)
  const zoneRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const el = zoneRef.current

    if (!el) {
      return
    }

    return registerCategoryDropZone(categoryId, {
      element: el,
      onDrop: payload => onDropSession(payload.id, categoryId),
      setIsOver
    })
  }, [categoryId, onDropSession])

  return (
    <div
      className={cn(
        'rounded-md transition-colors duration-150',
        isOver && 'bg-(--ui-control-active-background) ring-1 ring-(--ui-stroke-primary)',
        className
      )}
      ref={zoneRef}
    >
      {children}
    </div>
  )
}
