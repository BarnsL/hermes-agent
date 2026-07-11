import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useVirtualizer } from '@tanstack/react-virtual'
import { type FC, useCallback, useLayoutEffect, useRef, useState } from 'react'

import type { SessionInfo } from '@/hermes'
import { type SidebarSessionEntry } from '@/lib/session-branch-tree'
import { cn } from '@/lib/utils'
import { sessionPinId } from '@/store/session'

import { SidebarSessionRow } from './session-row'

interface SessionRowCommonProps {
  branchStem?: string
  isPinned: boolean
  isSelected: boolean
  isWorking: boolean
  onArchive: () => void
  onBranch?: () => void
  onDelete: () => void
  onPin: () => void
  onResume: () => void
  reorderable?: boolean
}

interface VirtualSessionListProps {
  activeSessionId: null | string
  className?: string
  entries: SidebarSessionEntry[]
  /** When provided, the virtualizer uses this as its scroll element instead of
   *  owning its own overflow container. Use this to unify scrolling with
   *  sibling content (categories, pinned) in a shared scroll container. */
  getScrollElement?: () => HTMLElement | null
  onArchiveSession: (sessionId: string) => void
  onBranchSession?: (sessionId: string, profile?: string) => void
  onDeleteSession: (sessionId: string) => void
  onResumeSession: (sessionId: string) => void
  onTogglePin: (sessionId: string) => void
  pinned: boolean
  sortable: boolean
  workingSessionIdSet: Set<string>
}

const ROW_ESTIMATE_PX = 28
const OVERSCAN_ROWS = 12
// The row grid renders with `gap-px`; the virtualizer must be told about it or
// every row's computed start drifts 1px further from reality (N-1 px of error
// across the list), which reads as rows popping in late / blank tail space.
const ROW_GAP_PX = 1

export const VirtualSessionList: FC<VirtualSessionListProps> = ({
  activeSessionId,
  className,
  entries,
  getScrollElement: getScrollElementProp,
  onArchiveSession,
  onBranchSession,
  onDeleteSession,
  onResumeSession,
  onTogglePin,
  pinned,
  sortable,
  workingSessionIdSet
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null)

  // When an external scroll element is provided (shared-scroll mode), use it
  // instead of owning our own overflow container. This lets categories, pinned
  // sessions, and virtualized recents all scroll together in one container.
  const sharedScroll = Boolean(getScrollElementProp)
  const resolvedGetScrollElement = getScrollElementProp ?? (() => containerRef.current)

  // ─── CRITICAL ISSUE #15 (2026-07-09): SHARED-SCROLL OFFSET (scrollMargin) ──
  // In shared-scroll mode this list does NOT start at the scroll element's
  // top — Pinned / Categories render above it in the same scroller. The
  // virtualizer maps scrollTop → visible rows, so it must know that offset
  // (TanStack's `scrollMargin`) or the visible window is computed against the
  // wrong origin and rows unmount while still on screen.
  //
  // Re-measured after every commit (no dep array). This relies on an
  // INVARIANT: everything that changes the height of the content above must
  // flow through a render of the sidebar tree that contains this list. That
  // is why ChatSidebar owns the $sessionCategories subscription and passes
  // categories down as a prop — a sibling-only subscription would collapse a
  // category without re-running this measure, leaving a stale origin (found
  // in adversarial review). The setState is guarded to >=1px change, so this
  // cannot render-loop.
  const [sharedScrollMargin, setSharedScrollMargin] = useState(0)
  // One-shot retry for same-commit mounts: when the scroll container and this
  // list mount in the SAME commit (sidebar reopen), descendant layout effects
  // run before the ancestor ref attaches, so the first measure sees null.
  const [measureRetry, setMeasureRetry] = useState(0)
  const retriedRef = useRef(false)

  // Intentionally no dep array: the offset depends on SIBLING layout
  // (pinned/categories above), not on any prop or state of this component, so
  // it must re-measure after every commit. The >=1px setState guard prevents
  // an update loop.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (!sharedScroll) {
      return
    }

    const el = containerRef.current
    const scrollEl = getScrollElementProp?.()

    if (!el || !scrollEl) {
      // Ancestor ref not attached yet (same-commit mount). Force ONE
      // post-frame re-render so both this measure and the virtualizer's
      // getScrollElement bind; without it the list shows the initialRect
      // window until unrelated store churn re-renders the sidebar.
      if (!retriedRef.current) {
        retriedRef.current = true
        requestAnimationFrame(() => setMeasureRetry(n => n + 1))
      }

      return
    }

    const margin = Math.round(
      el.getBoundingClientRect().top - scrollEl.getBoundingClientRect().top + scrollEl.scrollTop
    )

    setSharedScrollMargin(prev => (Math.abs(prev - margin) < 1 ? prev : margin))
  })

  const scrollMargin = sharedScroll ? sharedScrollMargin : 0

  const virtualizer = useVirtualizer({
    count: entries.length,
    estimateSize: () => ROW_ESTIMATE_PX,
    gap: ROW_GAP_PX,
    getItemKey: index => entries[index]?.session.id ?? index,
    getScrollElement: resolvedGetScrollElement,
    // On scroll-element attach, virtual-core calls scrollToOffset with the
    // current offset, which defaults to initialOffset = 0 — in shared-scroll
    // mode that would yank the WHOLE sidebar to the top every time this list
    // (re)mounts while scrolled (section reopen, search crossing the
    // virtualization threshold). Seeding from the live scrollTop makes the
    // attach a no-op. (Same trick useWindowVirtualizer uses with scrollY.)
    initialOffset: () => resolvedGetScrollElement()?.scrollTop ?? 0,
    // jsdom-friendly default; the real rect takes over on first observe.
    initialRect: { height: 600, width: 240 },
    overscan: OVERSCAN_ROWS,
    scrollMargin
  })

  // Consume the retry counter so eslint keeps it and the re-render sticks.
  void measureRetry

  const virtualItems = virtualizer.getVirtualItems()
  const totalSize = virtualizer.getTotalSize()
  // Item `start`/`end` values include scrollMargin; getTotalSize() does not.
  // Convert to list-local space before deriving the spacer paddings.
  const firstStart = (virtualItems[0]?.start ?? scrollMargin) - scrollMargin
  const lastEnd = (virtualItems[virtualItems.length - 1]?.end ?? scrollMargin) - scrollMargin
  const paddingTop = Math.max(0, firstStart)
  const paddingBottom = Math.max(0, totalSize - lastEnd)

  const rows = virtualItems.map(virtualItem => {
    const entry = entries[virtualItem.index]

    if (!entry) {
      return null
    }

    const { branchStem, session } = entry
    const reorderable = sortable && !branchStem

    const commonProps: SessionRowCommonProps = {
      branchStem,
      isPinned: pinned,
      isSelected: session.id === activeSessionId,
      isWorking: workingSessionIdSet.has(session.id),
      onArchive: () => onArchiveSession(session.id),
      onBranch: onBranchSession ? () => onBranchSession(session.id, session.profile) : undefined,
      onDelete: () => onDeleteSession(session.id),
      onPin: () => onTogglePin(sessionPinId(session)),
      onResume: () => onResumeSession(session.id),
      reorderable
    }

    return reorderable ? (
      <VirtualSortableRow
        index={virtualItem.index}
        key={session.id}
        measureRef={virtualizer.measureElement}
        rowProps={commonProps}
        session={session}
      />
    ) : (
      <SidebarSessionRow
        {...commonProps}
        data-index={virtualItem.index}
        key={session.id}
        ref={virtualizer.measureElement}
        session={session}
      />
    )
  })

  // When sortable, the caller wraps this in a ReorderableList that owns the
  // DndContext + SortableContext (keyed on the same ids); the virtualized rows
  // just consume that context via useSortable.
  // When an external scroll element is provided (shared-scroll mode), the
  // outer container handles scrolling — we just render the spacer rows.
  // Otherwise we own our own overflow-y-auto scroll container.
  const ownsScroll = !sharedScroll

  return (
    <div
      className={cn(
        'relative min-h-0',
        // ─── CRITICAL ISSUE #15: in shared-scroll mode this wrapper must carry
        // NO overflow property at all — not even overflow-x-hidden. Setting
        // overflow on one axis computes the other axis from `visible` to
        // `auto` (CSS spec), which silently turns the wrapper back into a
        // scroll container. A nested scroll container is what caused the
        // "sidebar won't scroll unless the cursor is on the scrollbar" dead
        // zone: Chromium latches wheel events to the nearest scroll container
        // under the cursor, and with overscroll-contain the chain stopped
        // there even though the wrapper had nothing to scroll. The outer
        // sidebar scroller already clips X for everyone. When this list OWNS
        // its scroller (no external scroll element), the old classes apply.
        // See CRITICAL-ISSUES.md #15.
        ownsScroll && 'flex-1 overflow-x-hidden overflow-y-auto overscroll-contain',
        className
      )}
      ref={containerRef}
    >
      <div className="grid gap-px" style={{ paddingBottom: `${paddingBottom}px`, paddingTop: `${paddingTop}px` }}>
        {rows}
      </div>
    </div>
  )
}

interface VirtualSortableRowProps {
  index: number
  measureRef: (node: Element | null) => void
  rowProps: SessionRowCommonProps
  session: SessionInfo
}

function VirtualSortableRow({ index, measureRef, rowProps, session }: VirtualSortableRowProps) {
  const { attributes, isDragging, listeners, setNodeRef, transform, transition } = useSortable({ id: session.id })

  // Merge dnd-kit's setNodeRef with the virtualizer's measureElement so
  // the row participates in both DnD hit-testing and TanStack height
  // measurement.
  const refMerged = useCallback(
    (node: HTMLDivElement | null) => {
      setNodeRef(node)
      measureRef(node)
    },
    [measureRef, setNodeRef]
  )

  return (
    <SidebarSessionRow
      {...rowProps}
      data-index={index}
      dragging={isDragging}
      dragHandleProps={{ ...attributes, ...listeners }}
      ref={refMerged}
      reorderable
      session={session}
      style={{ transform: CSS.Transform.toString(transform), transition }}
    />
  )
}
