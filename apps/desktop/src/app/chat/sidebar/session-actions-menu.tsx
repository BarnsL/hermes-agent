import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  closeAllTreeTabs,
  closeOtherTreeTabs,
  closeTreeTabsToRight,
  treeTabCloseTargets
} from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ColorSwatches } from '@/components/ui/color-swatches'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger
} from '@/components/ui/context-menu'
import { CopyButton } from '@/components/ui/copy-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { renameSession } from '@/hermes'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { PROFILE_SWATCHES } from '@/lib/profile-color'
import { exportSession } from '@/lib/session-export'
import { activeGateway } from '@/store/gateway'
import {
  $sessionCategories,
  createCategory,
  moveSessionToCategory,
  removeSessionFromCategory,
  type SessionCategory,
  setEditingCategoryId
} from '@/store/layout'
import { notify, notifyError } from '@/store/notifications'
import {
  $activeSessionId,
  $selectedStoredSessionId,
  $sessions,
  sessionMatchesStoredId,
  sessionPinId,
  setSessions
} from '@/store/session'
import { $sessionColorOverrides, setSessionColorOverride } from '@/store/session-color'
import { $sessionTiles, openSessionTile } from '@/store/session-states'
import { canOpenSessionWindow, openSessionInNewWindow } from '@/store/windows'

import type { SessionTitleResponse } from '../../types'

// Rename a session, preferring the gateway's session.title RPC over REST.
//
// A freshly *branched* session (and any brand-new chat) lives only in the
// gateway's in-memory _sessions map keyed by its RUNTIME id — no row is
// persisted to state.db until the first turn. REST PATCH /api/sessions/{id}
// resolves against the stored sessions table, so it 404s ("Session not found")
// on these runtime-only sessions. The session.title RPC resolves the live
// runtime session AND persists the row on demand, so it succeeds where REST
// cannot. This mirrors the /title slash command's fix (use-prompt-actions.ts).
//
// We only take the RPC path for the ACTIVE/selected session: its runtime id is
// known ($activeSessionId) and it lives on the active gateway, so there is no
// profile-routing ambiguity. Every other row (already persisted, possibly on a
// background profile) keeps the REST path, which handles profile scoping and a
// non-empty title is required by the RPC (it rejects clears), so clears stay on
// REST too.
export async function renameSessionPreferringRpc(
  storedSessionId: string,
  title: string,
  profile?: string
): Promise<{ title?: string }> {
  const isActiveRow = storedSessionId === $selectedStoredSessionId.get()
  const runtimeId = isActiveRow ? $activeSessionId.get() : null
  const gateway = activeGateway()

  if (title && runtimeId && gateway) {
    try {
      const result = await gateway.request<SessionTitleResponse>('session.title', {
        session_id: runtimeId,
        title
      })

      return { title: result?.title ?? title }
    } catch (err) {
      // Fall through to REST — e.g. the socket is mid-reconnect. REST still
      // works for any session that already has a persisted row. Log so a
      // genuine RPC-side failure (which then surfaces a REST 404 for the
      // runtime id) is at least diagnosable instead of silently swallowed.
      console.warn('session.title RPC rename failed; falling back to REST', err)
    }
  }

  return renameSession(storedSessionId, title, profile)
}

interface SessionActions {
  sessionId: string
  title: string
  pinned?: boolean
  profile?: string
  onPin?: () => void
  onBranch?: () => void
  onArchive?: () => void
  onDelete?: () => void
  /** Close this surface (a tile tab) — omitted where nothing closes (sidebar
   *  rows, the main tab). */
  onClose?: () => void
  /** TAB surfaces: the session is already a tab, so "Open in new tab" is
   *  nonsense there — sidebar rows/dropdowns keep it. */
  surface?: 'row' | 'tab'
  /** The tab's layout-tree pane id (`session-tile:<id>` or `workspace`) — enables
   *  the Close-others / to-the-right / all tab verbs. Tab surfaces only. */
  tabPaneId?: string
  /** The MAIN tab's escape hatch: hide the zone's tab bar (it sticky-shows
   *  once a tab is ever gained; this is the explicit off switch). */
  onHideTabBar?: () => void
}

type MenuItem = typeof DropdownMenuItem | typeof ContextMenuItem

/** A menu flavour (dropdown / context) — item + separator + submenu components. */
interface MenuKit {
  Item: MenuItem
  Separator: typeof DropdownMenuSeparator | typeof ContextMenuSeparator
  Sub: typeof DropdownMenuSub | typeof ContextMenuSub
  SubTrigger: typeof DropdownMenuSubTrigger | typeof ContextMenuSubTrigger
  SubContent: typeof DropdownMenuSubContent | typeof ContextMenuSubContent
}

const DROPDOWN_KIT: MenuKit = {
  Item: DropdownMenuItem,
  Separator: DropdownMenuSeparator,
  Sub: DropdownMenuSub,
  SubContent: DropdownMenuSubContent,
  SubTrigger: DropdownMenuSubTrigger
}

const CONTEXT_KIT: MenuKit = {
  Item: ContextMenuItem,
  Separator: ContextMenuSeparator,
  Sub: ContextMenuSub,
  SubContent: ContextMenuSubContent,
  SubTrigger: ContextMenuSubTrigger
}

interface ItemSpec {
  className?: string
  disabled: boolean
  icon: string
  label: string
  onSelect: (event: Event) => void
  variant?: 'destructive'
}

// The color picker inside the session menu's Appearance submenu. Its own
// component so only an OPEN submenu subscribes to the stores (not every row's
// menu). Reads/writes the override keyed by the DURABLE id so a color survives
// compression; clearing falls back to the inherited project color.
function SessionColorSwatches({ sessionId }: { sessionId: string }) {
  const { t } = useI18n()
  const overrides = useStore($sessionColorOverrides)
  const session = useStore($sessions).find(s => sessionMatchesStoredId(s, sessionId))
  const durableId = session ? sessionPinId(session) : sessionId

  return (
    <ColorSwatches
      clearIcon="circle-slash"
      clearLabel={t.sidebar.projects.noColor}
      onChange={color => setSessionColorOverride(durableId, color)}
      swatches={PROFILE_SWATCHES}
      value={overrides[durableId] ?? null}
    />
  )
}

// Last-resort category name. Every shipped locale defines
// sidebar.categories.defaultName, so this only guards a locale regressing to a
// blank string — the store's sanitizer accepts an empty name (it only checks
// `typeof name === 'string'`), which would persist an unnameable, unlabelled
// category the user cannot tell apart from its siblings.
const FALLBACK_CATEGORY_NAME = 'New category'

// The category a session currently lives in, or undefined when it is
// uncategorized. `find` is exhaustive because membership is exactly ONE
// category: moveSessionToCategory strips the id from every other category in
// the same write. Takes the DURABLE session id. Exported for tests.
export function categoryForSession(categories: SessionCategory[], durableId: string): SessionCategory | undefined {
  return categories.find(category => category.sessionIds.includes(durableId))
}

// Create a category and file the session into it in one gesture, returning the
// new category so the caller can put it straight into rename mode.
//
// The order is load-bearing: createCategory commits synchronously (nanostores
// `.set`), so moveSessionToCategory's "does the target exist?" guard sees the
// category that was just minted. `name` is the localized default — trimmed and
// backstopped so a category can never be created blank. Exported for tests.
export function createCategoryForSession(durableId: string, name: string): SessionCategory {
  const category = createCategory(name.trim() || FALLBACK_CATEGORY_NAME)

  moveSessionToCategory(durableId, category.id)

  return category
}

// The "Move to category" submenu body. Its own component so only an OPEN
// submenu subscribes to the stores — the menu is rendered per sidebar row AND
// twice per row (context menu + dropdown), so subscribing in useSessionActions
// would re-render every visible row's menus on any category edit. Same
// rationale as SessionColorSwatches above.
function SessionCategoryItems({ kit, sessionId }: { kit: MenuKit; sessionId: string }) {
  const { t } = useI18n()
  const c = t.sidebar.categories
  const categories = useStore($sessionCategories)
  const session = useStore($sessions).find(s => sessionMatchesStoredId(s, sessionId))
  // Membership is keyed on the DURABLE id (the lineage root), like pins:
  // filing the LIVE id would evaporate at the next auto-compression. Same
  // resolution the drop handler does in session-categories-section.tsx.
  const durableId = session ? sessionPinId(session) : sessionId
  const current = categoryForSession(categories, durableId)

  return (
    <>
      {categories.length === 0 ? (
        // A plain div rather than a disabled Item: Radix's roving focus skips
        // disabled items, so keyboard focus lands straight on "New category…"
        // instead of stalling on an inert empty-state row.
        <div className="px-2 py-1 text-xs text-(--ui-text-tertiary)">{c.moveToEmpty}</div>
      ) : (
        categories.map(category => (
          <kit.Item
            // ContextMenu has no CheckboxItem/RadioItem primitive (DropdownMenu
            // does), and ONE definition serves both flavours — so the checked
            // state is announced by hand and drawn with the same check Codicon
            // DropdownMenuCheckboxItem renders internally.
            aria-checked={category.id === current?.id}
            key={category.id}
            onSelect={() => {
              triggerHaptic('selection')

              // Re-selecting the current category would only shuffle the
              // session to the tail of that category's membership.
              if (category.id === current?.id) {
                return
              }

              moveSessionToCategory(durableId, category.id)
            }}
            role="menuitemradio"
          >
            <Codicon name="folder" size="0.875rem" />
            <span className="min-w-0 flex-1 truncate">{category.name}</span>
            {category.id === current?.id && <Codicon className="ml-auto" name="check" size="0.75rem" />}
          </kit.Item>
        ))
      )}
      <kit.Separator />
      <kit.Item
        onSelect={() => {
          triggerHaptic('selection')

          const category = createCategoryForSession(durableId, c.defaultName)

          // Hand the fresh category to the sidebar's EXISTING inline-rename
          // input rather than opening a second naming UI — same end state as
          // the Categories "+" button.
          //
          // Deferred by a tick because Radix returns focus to the menu trigger
          // as the menu closes: mounting the autoFocus rename input in this
          // same commit would let that focus return blur it immediately, which
          // commits the rename and makes the input flash and vanish.
          window.setTimeout(() => setEditingCategoryId(category.id), 0)
        }}
      >
        <Codicon name="new-folder" size="0.875rem" />
        <span>{c.moveToNew}</span>
      </kit.Item>
      {current && (
        // Not destructive: the category and the session both survive, only the
        // membership goes — so no red, unlike the category header's Delete.
        <kit.Item
          onSelect={() => {
            triggerHaptic('selection')
            removeSessionFromCategory(durableId, current.id)
          }}
        >
          <Codicon name="circle-slash" size="0.875rem" />
          <span>{c.removeFrom}</span>
        </kit.Item>
      )}
    </>
  )
}

function useSessionActions({
  sessionId,
  title,
  pinned = false,
  profile,
  onPin,
  onBranch,
  onArchive,
  onDelete,
  onClose,
  onHideTabBar,
  surface = 'row',
  tabPaneId
}: SessionActions) {
  const { t } = useI18n()
  const r = t.sidebar.row
  const [renameOpen, setRenameOpen] = useState(false)
  const tiles = useStore($sessionTiles)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)

  // Already showing as a tab somewhere (a tile, or loaded in main — main IS
  // a tab): offering "Open in new tab" again is noise.
  const alreadyTabbed = sessionId === selectedStoredSessionId || tiles.some(tile => tile.storedSessionId === sessionId)

  const spec = (partial: Omit<ItemSpec, 'onSelect'> & { onSelect: () => void }): ItemSpec => partial

  // OPEN — where else this session can go. A tab surface IS a tab already,
  // so it only offers the window hop (and its own Close, below).
  const openItems: ItemSpec[] = [
    ...(surface === 'row' && !alreadyTabbed
      ? [
          spec({
            disabled: !sessionId,
            icon: 'browser',
            label: r.openInNewTab,
            onSelect: () => {
              triggerHaptic('selection')
              // Stack into the MAIN zone as a tab (center dock; the strip
              // sticky-shows on gain) — the door to the tab bar.
              openSessionTile(sessionId, 'center')
            }
          })
        ]
      : []),
    ...(canOpenSessionWindow()
      ? [
          spec({
            disabled: !sessionId,
            icon: 'link-external',
            label: r.newWindow,
            onSelect: () => {
              triggerHaptic('selection')
              void openSessionInNewWindow(sessionId)
            }
          })
        ]
      : [])
  ]

  // IDENTITY — name/mark/reference the session.
  const identityItems: ItemSpec[] = [
    spec({
      disabled: !sessionId,
      icon: 'edit',
      label: r.rename,
      onSelect: () => {
        triggerHaptic('selection')
        setRenameOpen(true)
      }
    }),
    spec({
      disabled: !onPin,
      icon: 'pin',
      label: pinned ? r.unpin : r.pin,
      onSelect: () => {
        triggerHaptic('selection')
        onPin?.()
      }
    })
  ]

  // WORK — derive/extract from the session.
  const workItems: ItemSpec[] = [
    spec({
      disabled: !onBranch,
      icon: 'git-branch',
      label: r.branchFrom,
      onSelect: () => {
        triggerHaptic('selection')
        onBranch?.()
      }
    }),
    spec({
      disabled: !sessionId,
      icon: 'cloud-download',
      label: r.export,
      onSelect: () => {
        triggerHaptic('selection')
        void exportSession(sessionId, { profile, title })
      }
    })
  ]

  // TAB — close verbs that act on the strip (tabs only; a row isn't a tab).
  const closeTargets = surface === 'tab' && tabPaneId ? treeTabCloseTargets(tabPaneId) : null

  const tabCloseItems: ItemSpec[] =
    surface === 'tab'
      ? [
          ...(onClose
            ? [
                spec({
                  disabled: false,
                  icon: 'close',
                  label: t.common.close,
                  onSelect: () => {
                    triggerHaptic('selection')
                    onClose()
                  }
                })
              ]
            : []),
          ...(tabPaneId
            ? [
                spec({
                  disabled: !closeTargets?.others,
                  icon: 'close-all',
                  label: t.zones.closeOthers,
                  onSelect: () => {
                    triggerHaptic('selection')
                    closeOtherTreeTabs(tabPaneId)
                  }
                }),
                spec({
                  disabled: !closeTargets?.right,
                  icon: 'arrow-right',
                  label: t.zones.closeToRight,
                  onSelect: () => {
                    triggerHaptic('selection')
                    closeTreeTabsToRight(tabPaneId)
                  }
                }),
                spec({
                  disabled: !closeTargets?.all,
                  icon: 'clear-all',
                  label: t.zones.closeAll,
                  onSelect: () => {
                    triggerHaptic('selection')
                    closeAllTreeTabs(tabPaneId)
                  }
                })
              ]
            : [])
        ]
      : []

  // DANGER — put it away / destroy it (delete stays last, destructive-red).
  const dangerItems: ItemSpec[] = [
    spec({
      disabled: !onArchive,
      icon: 'archive',
      label: r.archive,
      onSelect: () => {
        triggerHaptic('selection')
        onArchive?.()
      }
    }),
    {
      className: 'text-destructive focus:text-destructive',
      disabled: !onDelete,
      icon: 'trash',
      label: t.common.delete,
      onSelect: () => {
        triggerHaptic('warning')
        onDelete?.()
      },
      variant: 'destructive'
    }
  ]

  const renderMenuItem = (Item: MenuItem, { className, disabled, icon, label, onSelect, variant }: ItemSpec) => (
    <Item className={className} disabled={disabled} key={label} onSelect={onSelect} variant={variant}>
      <Codicon name={icon} size="0.875rem" />
      <span>{label}</span>
    </Item>
  )

  const renderItems = (kit: MenuKit) => (
    <>
      {openItems.map(item => renderMenuItem(kit.Item, item))}
      {openItems.length > 0 && <kit.Separator />}
      {identityItems.map(item => renderMenuItem(kit.Item, item))}
      {/* Filing verbs sit together: pin (identityItems, above) and category. */}
      <kit.Sub>
        <kit.SubTrigger disabled={!sessionId}>
          <Codicon name="folder" size="0.875rem" />
          <span>{t.sidebar.categories.moveTo}</span>
        </kit.SubTrigger>
        <kit.SubContent className="w-52">
          <SessionCategoryItems kit={kit} sessionId={sessionId} />
        </kit.SubContent>
      </kit.Sub>
      <kit.Sub>
        <kit.SubTrigger disabled={!sessionId}>
          <Codicon name="symbol-color" size="0.875rem" />
          <span>{t.sidebar.projects.menuAppearance}</span>
        </kit.SubTrigger>
        <kit.SubContent className="p-2">
          <SessionColorSwatches sessionId={sessionId} />
        </kit.SubContent>
      </kit.Sub>
      <CopyButton
        appearance={kit.Item === DropdownMenuItem ? 'menu-item' : 'context-menu-item'}
        disabled={!sessionId}
        errorMessage={r.copyIdFailed}
        iconClassName="size-3.5 text-current"
        key={r.copyId}
        label={r.copyId}
        onCopyError={err => notifyError(err, r.copyIdFailed)}
        text={sessionId}
      />
      <kit.Separator />
      {workItems.map(item => renderMenuItem(kit.Item, item))}
      {tabCloseItems.length > 0 && (
        <>
          <kit.Separator />
          {tabCloseItems.map(item => renderMenuItem(kit.Item, item))}
        </>
      )}
      <kit.Separator />
      {dangerItems.map(item => renderMenuItem(kit.Item, item))}
      {onHideTabBar && (
        <>
          <kit.Separator />
          {renderMenuItem(kit.Item, {
            disabled: false,
            icon: 'eye-closed',
            label: r.hideTabBar,
            onSelect: () => {
              triggerHaptic('selection')
              onHideTabBar()
            }
          })}
        </>
      )}
    </>
  )

  const renameDialog = (
    <RenameSessionDialog
      currentTitle={title}
      onOpenChange={setRenameOpen}
      open={renameOpen}
      profile={profile}
      sessionId={sessionId}
    />
  )

  return { renameDialog, renderItems }
}

interface SessionActionsMenuProps
  extends SessionActions, Pick<React.ComponentProps<typeof DropdownMenuContent>, 'align' | 'sideOffset'> {
  children: React.ReactNode
}

export function SessionActionsMenu({ children, align = 'end', sideOffset = 6, ...actions }: SessionActionsMenuProps) {
  const { t } = useI18n()
  const { renameDialog, renderItems } = useSessionActions(actions)
  const [open, setOpen] = useState(false)

  return (
    <>
      <DropdownMenu onOpenChange={setOpen} open={open}>
        <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
        <DropdownMenuContent
          align={align}
          aria-label={t.sidebar.row.actionsFor(actions.title)}
          className="w-40"
          sideOffset={sideOffset}
        >
          {renderItems(DROPDOWN_KIT)}
        </DropdownMenuContent>
      </DropdownMenu>
      {renameDialog}
    </>
  )
}

interface SessionContextMenuProps extends SessionActions {
  children: React.ReactNode
}

export function SessionContextMenu({ children, ...actions }: SessionContextMenuProps) {
  const { t } = useI18n()
  const { renameDialog, renderItems } = useSessionActions(actions)

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
        <ContextMenuContent aria-label={t.sidebar.row.actionsFor(actions.title)} className="w-40">
          {renderItems(CONTEXT_KIT)}
        </ContextMenuContent>
      </ContextMenu>
      {renameDialog}
    </>
  )
}

interface RenameSessionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  sessionId: string
  currentTitle: string
  profile?: string
}

function RenameSessionDialog({ open, onOpenChange, sessionId, currentTitle, profile }: RenameSessionDialogProps) {
  const { t } = useI18n()
  const r = t.sidebar.row
  const [value, setValue] = useState(currentTitle)
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setValue(currentTitle)
      window.setTimeout(() => inputRef.current?.select(), 0)
    }
  }, [currentTitle, open])

  const submit = async () => {
    const next = value.trim()

    if (!sessionId || submitting) {
      return
    }

    if (next === currentTitle.trim()) {
      onOpenChange(false)

      return
    }

    setSubmitting(true)

    try {
      const result = await renameSessionPreferringRpc(sessionId, next, profile)
      const finalTitle = result.title || next || ''
      setSessions(prev => prev.map(s => (s.id === sessionId ? { ...s, title: finalTitle || null } : s)))
      notify({ durationMs: 2_000, kind: 'success', message: r.renamed })
      onOpenChange(false)
    } catch (err) {
      notifyError(err, r.renameFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{r.renameTitle}</DialogTitle>
          <DialogDescription>{r.renameDesc}</DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          disabled={submitting}
          onChange={event => setValue(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              event.preventDefault()
              void submit()
            } else if (event.key === 'Escape') {
              onOpenChange(false)
            }
          }}
          placeholder={r.untitledPlaceholder}
          ref={inputRef}
          value={value}
        />
        <DialogFooter>
          <Button disabled={submitting} onClick={() => onOpenChange(false)} type="button" variant="ghost">
            {t.common.cancel}
          </Button>
          <Button disabled={submitting} onClick={() => void submit()} type="button">
            {t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
