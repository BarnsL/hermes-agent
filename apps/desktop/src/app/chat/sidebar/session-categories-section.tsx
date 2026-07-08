import { useStore } from '@nanostores/react'
import { useState } from 'react'
import type * as React from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import type { SessionInfo } from '@/hermes'
import {
  $sessionCategories,
  createCategory,
  deleteCategory,
  moveSessionToCategory,
  removeSessionFromCategory,
  renameCategory,
  toggleCategoryCollapsed,
} from '@/store/layout'
import { pinSession } from '@/store/layout'

import { CategoryDropZone } from './category-drop-zone'
import { SidebarSessionsSection } from './sessions-section'

export function SessionCategoriesSection({
  activeSessionId,
  sessionById,
  workingSessionIdSet,
  onArchiveSession,
  onBranchSession,
  onDeleteSession,
  onResumeSession,
}: {
  activeSessionId: string | null
  sessionById: Map<string, SessionInfo>
  workingSessionIdSet: Set<string>
  onArchiveSession: (id: string) => void
  onBranchSession: (id: string, profile?: string) => void
  onDeleteSession: (id: string) => void
  onResumeSession: (id: string) => void
}) {
  const categories = useStore($sessionCategories)

  if (categories.length === 0) {
    return (
      <div className="shrink-0 px-2 pb-1">
        <div className="flex items-center gap-1">
          <span className="text-[0.625rem] font-semibold text-(--ui-text-tertiary) uppercase tracking-wider flex-1">
            Categories
          </span>
          <Button
            aria-label="New Category" className="h-5 w-5" onClick={() => createCategory('New Category')}
            size="icon"
            variant="ghost"
          >
            <Codicon name="plus" size="0.75rem" />
          </Button>
        </div>
      </div>
    )
  }

  return (
    <>
      {/* Categories header */}
      <div className="shrink-0 px-2 pb-1">
        <div className="flex items-center gap-1">
          <span className="text-[0.625rem] font-semibold text-(--ui-text-tertiary) uppercase tracking-wider flex-1">
            Categories
          </span>
          <Button
            aria-label="New Category" className="h-5 w-5" onClick={() => createCategory('New Category')}
            size="icon"
            variant="ghost"
          >
            <Codicon name="plus" size="0.75rem" />
          </Button>
        </div>
      </div>

      {/* Individual categories */}
      {categories.map(cat => (
        <CategorySection
          activeSessionId={activeSessionId}
          cat={cat}
          key={cat.id}
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
  cat,
  sessionById,
  activeSessionId,
  workingSessionIdSet,
  onArchiveSession,
  onBranchSession,
  onDeleteSession,
  onResumeSession,
}: {
  cat: { id: string; name: string; sessionIds: string[]; collapsed?: boolean }
  sessionById: Map<string, SessionInfo>
  activeSessionId: string | null
  workingSessionIdSet: Set<string>
  onArchiveSession: (id: string) => void
  onBranchSession: (id: string, profile?: string) => void
  onDeleteSession: (id: string) => void
  onResumeSession: (id: string) => void
}) {
  const [editingCat, setEditingCat] = useState<string | null>(null)
  const [editCatName, setEditCatName] = useState('')

  const catSessions = cat.sessionIds
    .map(id => sessionById.get(id))
    .filter((s): s is SessionInfo => !!s)

  const categoryMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button className="h-5 w-5 shrink-0 opacity-0 group-hover/section:opacity-100 focus-visible:opacity-100" size="icon" variant="ghost">
          <Codicon name="kebab-vertical" size="0.875rem" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        <DropdownMenuItem className="gap-2 rounded-none px-2.5 py-1 text-xs"
          onSelect={() => { setEditingCat(cat.id); setEditCatName(cat.name) }}>
          <Codicon name="edit" size="0.875rem" />
          <span>Rename</span>
        </DropdownMenuItem>
        <DropdownMenuItem className="gap-2 rounded-none px-2.5 py-1 text-xs text-destructive focus:text-destructive"
          onSelect={() => deleteCategory(cat.id)}>
          <Codicon name="trash" size="0.875rem" />
          <span>Delete</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  return (
    <div className="pb-2">
      {editingCat === cat.id ? (
        <div className="flex items-center gap-1 px-2 pb-1 pt-1.5">
          <Input
            autoFocus className="h-5 text-xs px-1 py-0 flex-1"
            onBlur={() => {
              if (editCatName.trim()) {renameCategory(cat.id, editCatName.trim())}
              setEditingCat(null)
            }}
            onChange={e => setEditCatName(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { renameCategory(cat.id, editCatName.trim()); setEditingCat(null) }

              if (e.key === 'Escape') {setEditingCat(null)}
            }}
            value={editCatName}
          />
        </div>
      ) : (
        <CategoryDropZone
          categoryId={cat.id}
          onDropSession={(sessionId, catId) => moveSessionToCategory(sessionId, catId)}
        >
          <SidebarSessionsSection
            activeSessionId={activeSessionId}
            contentClassName="flex flex-col gap-px pb-0.5"
            emptyState={
              <div className="grid min-h-8 place-items-center rounded-lg px-2 text-center text-[0.625rem] text-(--ui-text-tertiary) italic">
                Drop sessions here
              </div>
            }
            headerAction={categoryMenu}
            headerClassName="pb-0.5 pt-1"
            label={cat.name}
            labelMeta={<span className="tabular-nums">{catSessions.length}</span>}
            onArchiveSession={onArchiveSession}
            onBranchSession={onBranchSession}
            onDeleteSession={onDeleteSession}
            onResumeSession={onResumeSession}
            onToggle={() => toggleCategoryCollapsed(cat.id)}
            onTogglePin={id => {
              removeSessionFromCategory(id, cat.id)
              pinSession(id)
            }}
            open={!cat.collapsed}
            pinned={false}
            rootClassName="p-0 pb-0.5"
            sessions={catSessions}
            sortable={false}
            workingSessionIdSet={workingSessionIdSet}
          />
        </CategoryDropZone>
      )}
    </div>
  )
}
