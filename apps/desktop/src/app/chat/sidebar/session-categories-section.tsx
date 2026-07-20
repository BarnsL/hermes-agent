import { useLayoutEffect, useRef, useState } from 'react'
import type * as React from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import type { SessionInfo } from '@/hermes'
import { useI18n } from '@/i18n'
import {
  createCategory,
  deleteCategory,
  moveSessionToCategory,
  pinSession,
  removeSessionFromCategory,
  renameCategory,
  type SessionCategory,
  setEditingCategoryId,
  toggleCategoryCollapsed
} from '@/store/layout'
import { sessionPinId } from '@/store/session'

import { CategoryDropZone } from './category-drop-zone'
import { SidebarSessionsSection } from './sessions-section'

// User-defined session buckets, rendered between Pinned and Recents. Drop a
// session row onto a category (the shared pointer drag session — see
// category-drop-zone.tsx and app/chat/session-drag.ts) to file it there.
// Categories are a VIEW over sessions: the flat recents list still shows
// everything.
export function SessionCategoriesSection({
  activeSessionId,
  categories,
  editingCategoryId,
  onArchiveSession,
  onBranchSession,
  onDeleteSession,
  onResumeSession,
  sessionById,
  workingSessionIdSet
}: {
  activeSessionId: null | string
  // Both `categories` and `editingCategoryId` are passed down from
  // ChatSidebar (which owns the store subscriptions) instead of subscribing
  // here: the virtualized recents list below re-measures its shared-scroll
  // offset per commit of the sidebar tree, so ANY category height change
  // (expand/collapse, add/remove, rename-input swap) MUST re-render that
  // tree — see VirtualSessionList's scrollMargin invariant.
  categories: SessionCategory[]
  editingCategoryId: null | string
  onArchiveSession: (id: string) => void
  onBranchSession: (id: string, profile?: string) => void
  onDeleteSession: (id: string) => void
  onResumeSession: (id: string) => void
  sessionById: Map<string, SessionInfo>
  workingSessionIdSet: Set<string>
}) {
  const { t } = useI18n()
  const c = t.sidebar.categories

  const header = (
    <div className="shrink-0 px-2 pb-1">
      <div className="flex items-center gap-1">
        <span className="flex-1 text-[0.625rem] font-semibold uppercase tracking-wider text-(--ui-text-tertiary)">
          {c.sectionLabel}
        </span>
        <Button
          aria-label={c.newCategory}
          className="h-5 w-5"
          // Create AND name in one gesture: land the fresh category in the
          // inline-rename input instead of leaving a row called "New category"
          // for the user to hunt down and rename. Matches the session actions
          // menu's "New category…". No deferral needed here (unlike that menu):
          // a plain Button does not restore focus on click, so nothing competes
          // with the input's autoFocus.
          onClick={() => setEditingCategoryId(createCategory(c.defaultName).id)}
          size="icon"
          variant="ghost"
        >
          <Codicon name="plus" size="0.75rem" />
        </Button>
      </div>
    </div>
  )

  if (categories.length === 0) {
    return header
  }

  return (
    <>
      {header}
      {categories.map(category => (
        <CategorySection
          activeSessionId={activeSessionId}
          category={category}
          editing={editingCategoryId === category.id}
          key={category.id}
          onArchiveSession={onArchiveSession}
          onBranchSession={onBranchSession}
          onDeleteSession={onDeleteSession}
          onResumeSession={onResumeSession}
          sessionById={sessionById}
          workingSessionIdSet={workingSessionIdSet}
        />
      ))}
    </>
  )
}

function CategorySection({
  activeSessionId,
  category,
  editing,
  onArchiveSession,
  onBranchSession,
  onDeleteSession,
  onResumeSession,
  sessionById,
  workingSessionIdSet
}: {
  activeSessionId: null | string
  category: SessionCategory
  editing: boolean
  onArchiveSession: (id: string) => void
  onBranchSession: (id: string, profile?: string) => void
  onDeleteSession: (id: string) => void
  onResumeSession: (id: string) => void
  sessionById: Map<string, SessionInfo>
  workingSessionIdSet: Set<string>
}) {
  const { t } = useI18n()
  const c = t.sidebar.categories
  // The input's text is the only state kept local: keystrokes don't change
  // layout height, so they don't need to re-render the sidebar tree.
  const [editName, setEditName] = useState('')
  // Enter commits and closes the input, which fires blur — guard so the
  // commit runs once per edit session.
  const committedRef = useRef(false)

  // Members are stored as durable ids (lineage root — see sessionPinId) and
  // resolve through the live session map, which indexes both the durable and
  // the live id. Ids whose sessions aren't loaded don't render until the
  // post-refresh backfill fetches them by id (backfillKeptSessions in
  // use-session-list-actions.ts) — so a member aged past the recents page
  // reappears within one refresh instead of vanishing.
  const categorySessions = category.sessionIds
    .map(id => sessionById.get(id))
    .filter((session): session is SessionInfo => Boolean(session))

  // Rename mode can be entered from OUTSIDE this component — the session
  // actions menu's "New category…" creates a category and hands it straight to
  // this input — so seeding lives here rather than only in beginRename.
  //
  // Re-arming committedRef matters as much as the text: it is left true by any
  // previous commit on this instance, which would make the next external
  // rename silently no-op. useLayoutEffect (not useEffect) so the input never
  // paints a frame with stale/empty text.
  useLayoutEffect(() => {
    if (editing) {
      committedRef.current = false
      setEditName(category.name)
    }
    // Deliberately keyed on `editing` alone: re-seeding on every name change
    // would clobber what the user is typing, since commitRename writes the
    // name while this input is still mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing])

  const beginRename = () => {
    setEditingCategoryId(category.id)
  }

  const commitRename = () => {
    if (committedRef.current) {
      return
    }

    committedRef.current = true

    const name = editName.trim()

    if (name) {
      renameCategory(category.id, name)
    }

    setEditingCategoryId(null)
  }

  // Drops arrive with the drag payload's LIVE session id; store the durable
  // id so membership survives auto-compression rotating the live id.
  const fileSession = (sessionId: string) => {
    const session = sessionById.get(sessionId)

    moveSessionToCategory(session ? sessionPinId(session) : sessionId, category.id)
  }

  const categoryMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={c.menu}
          className="h-5 w-5 shrink-0 opacity-0 focus-visible:opacity-100 group-hover/section:opacity-100"
          size="icon"
          variant="ghost"
        >
          <Codicon name="kebab-vertical" size="0.875rem" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        <DropdownMenuItem className="gap-2 rounded-none px-2.5 py-1 text-xs" onSelect={beginRename}>
          <Codicon name="edit" size="0.875rem" />
          <span>{c.rename}</span>
        </DropdownMenuItem>
        <DropdownMenuItem
          className="gap-2 rounded-none px-2.5 py-1 text-xs text-destructive focus:text-destructive"
          onSelect={() => deleteCategory(category.id)}
        >
          <Codicon name="trash" size="0.875rem" />
          <span>{c.delete}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  if (editing) {
    return (
      <div className="flex items-center gap-1 px-2 pb-1 pt-1.5">
        <Input
          autoFocus
          className="h-5 flex-1 px-1 py-0 text-xs"
          onBlur={commitRename}
          onChange={e => setEditName(e.target.value)}
          onKeyDown={e => {
            // IME composition Enter confirms the conversion, not the rename.
            if (e.nativeEvent.isComposing) {
              return
            }

            if (e.key === 'Enter') {
              commitRename()
            }

            if (e.key === 'Escape') {
              committedRef.current = true
              setEditingCategoryId(null)
            }
          }}
          value={editName}
        />
      </div>
    )
  }

  return (
    <div className="pb-2">
      <CategoryDropZone categoryId={category.id} onDropSession={fileSession}>
        <SidebarSessionsSection
          activeSessionId={activeSessionId}
          contentClassName="flex flex-col gap-px pb-0.5"
          emptyState={
            <div className="grid min-h-8 place-items-center rounded-lg px-2 text-center text-[0.625rem] italic text-(--ui-text-tertiary)">
              {c.dropHint}
            </div>
          }
          headerAction={categoryMenu}
          label={category.name}
          labelMeta={<span className="tabular-nums">{categorySessions.length}</span>}
          onArchiveSession={onArchiveSession}
          onBranchSession={onBranchSession}
          onDeleteSession={onDeleteSession}
          onResumeSession={onResumeSession}
          onToggle={() => toggleCategoryCollapsed(category.id)}
          onTogglePin={id => {
            // Pinning from inside a category moves the session to Pinned.
            // `id` is already the durable pin id (sessions-section passes
            // sessionPinId), which matches the stored membership key.
            removeSessionFromCategory(id, category.id)
            pinSession(id)
          }}
          open={!category.collapsed}
          pinned={false}
          rootClassName="shrink-0 p-0 pb-0.5"
          sessions={categorySessions}
          sortable={false}
          workingSessionIdSet={workingSessionIdSet}
        />
      </CategoryDropZone>
    </div>
  )
}
