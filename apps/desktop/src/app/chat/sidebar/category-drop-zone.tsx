import { useDroppable } from '@dnd-kit/core'
import type * as React from 'react'
import { cn } from '@/lib/utils'
import { dragHasSession, readSessionDrag } from '@/app/chat/composer/inline-refs'

/**
 * Droppable wrapper for session categories.
 * Sessions are already draggable via session-row.tsx (HERMES_SESSION_MIME).
 * This component makes categories accept those drags via HTML5 DnD API.
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
  const { isOver, setNodeRef } = useDroppable({
    id: `category-${categoryId}`,
    data: { type: 'category', categoryId }
  })

  const handleDragOver = (e: React.DragEvent) => {
    if (dragHasSession(e.dataTransfer)) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const payload = readSessionDrag(e.dataTransfer)
    if (payload?.id) {
      onDropSession(payload.id, categoryId)
    }
  }

  return (
    <div
      ref={setNodeRef}
      className={cn(
        'rounded-md transition-colors duration-150',
        isOver && 'bg-(--ui-control-active-background) ring-1 ring-(--ui-stroke-primary)',
        className
      )}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {children}
    </div>
  )
}
