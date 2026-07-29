import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSub,
  DropdownMenuSubTrigger
} from '@/components/ui/dropdown-menu'
import type { SessionInfo } from '@/hermes'
import {
  $editingCategoryId,
  $sessionCategories,
  createCategory,
  moveSessionToCategory,
  setEditingCategoryId
} from '@/store/layout'
import { setSessions } from '@/store/session'

import { DROPDOWN_KIT, SessionActionsMenu, SessionCategoryItems, SessionContextMenu } from './session-actions-menu'

// The "Move to category" submenu, mounted for real. The pure-helper tests in
// session-actions-menu.test.ts cannot see any of this: deleting the whole
// <kit.Sub> block, or collapsing the live→durable id resolution to
// `const durableId = sessionId`, both leave that suite green.

const renameSession = vi.fn(async () => ({ ok: true, title: 'rest-title' }))

vi.mock('@/hermes', () => ({
  renameSession: (...args: unknown[]) => renameSession(...(args as [])),
  // profile.ts calls this at import (its $activeGatewayProfile subscribe fires
  // immediately), pulled in transitively via session-states.
  setApiRequestProfile: () => {},
  HermesGateway: class {}
}))

vi.mock('@/store/gateway', () => ({
  activeGateway: () => null
}))

// Radix calls these on open; jsdom doesn't implement them.
beforeAll(() => {
  Element.prototype.scrollIntoView ??= () => undefined
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.setPointerCapture ??= () => undefined
  Element.prototype.releasePointerCapture ??= () => undefined
})

const LIVE_ID = 'live-session-99'
const DURABLE_ID = 'lineage-root-1'

function session(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return { id: LIVE_ID, _lineage_root_id: DURABLE_ID, ...overrides } as SessionInfo
}

beforeEach(() => {
  $sessionCategories.set([])
  setSessions([])
  setEditingCategoryId(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Mount the submenu body inside an OPEN menu + sub, the way the real menu
 *  renders it. Mirrors the pattern in app/shell/model-edit-submenu.test.tsx. */
function renderItems(sessionId: string, suppressRef = { current: false }) {
  render(
    <DropdownMenu open>
      <DropdownMenuContent>
        <DropdownMenuSub open>
          <DropdownMenuSubTrigger>move</DropdownMenuSubTrigger>
          <SessionCategoryItems kit={DROPDOWN_KIT} sessionId={sessionId} suppressCloseAutoFocusRef={suppressRef} />
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  return suppressRef
}

describe('SessionCategoryItems', () => {
  it('renders one radio row per category, in store order, ticking the current one', () => {
    const work = createCategory('Work')
    createCategory('Play')
    moveSessionToCategory(DURABLE_ID, work.id)
    setSessions([session()])

    renderItems(LIVE_ID)

    const rows = screen.getAllByRole('menuitemradio')
    expect(rows.map(row => row.textContent)).toEqual(['Work', 'Play'])
    expect(rows[0]?.getAttribute('aria-checked')).toBe('true')
    expect(rows[1]?.getAttribute('aria-checked')).toBe('false')
  })

  it('files the session into a category that is clicked', () => {
    const work = createCategory('Work')
    setSessions([session()])

    renderItems(LIVE_ID)
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Work' }))

    expect($sessionCategories.get().find(c => c.id === work.id)?.sessionIds).toEqual([DURABLE_ID])
  })

  // The mutation this pins: `const durableId = sessionId`. Membership keyed on
  // the LIVE id looks right until auto-compression rotates it, then silently
  // vanishes — so assert the LINEAGE ROOT is what gets stored.
  it('stores the durable lineage-root id, not the live id it was handed', () => {
    const work = createCategory('Work')
    setSessions([session()])

    renderItems(LIVE_ID)
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Work' }))

    const stored = $sessionCategories.get().find(c => c.id === work.id)?.sessionIds
    expect(stored).toEqual([DURABLE_ID])
    expect(stored).not.toContain(LIVE_ID)
  })

  it('resolves the current category through the durable id too', () => {
    const work = createCategory('Work')
    moveSessionToCategory(DURABLE_ID, work.id)
    setSessions([session()])

    renderItems(LIVE_ID)

    expect(screen.getByRole('menuitemradio', { name: 'Work' }).getAttribute('aria-checked')).toBe('true')
  })

  it('falls back to the id it was given when the session is not loaded', () => {
    const work = createCategory('Work')
    // $sessions stays empty — the row's session aged past the loaded page.
    renderItems('orphan-session')
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Work' }))

    expect($sessionCategories.get().find(c => c.id === work.id)?.sessionIds).toEqual(['orphan-session'])
  })

  it('does not reorder membership when the current category is re-selected', () => {
    const work = createCategory('Work')
    moveSessionToCategory('other-session', work.id)
    moveSessionToCategory(DURABLE_ID, work.id)
    setSessions([session()])

    renderItems(LIVE_ID)
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Work' }))

    // Re-selecting would otherwise shuffle this session to the tail.
    expect($sessionCategories.get().find(c => c.id === work.id)?.sessionIds).toEqual(['other-session', DURABLE_ID])
  })

  it('renders the empty state — not a menu row — when no categories exist', () => {
    setSessions([session()])

    renderItems(LIVE_ID)

    expect(screen.getByText('No categories yet')).toBeTruthy()
    expect(screen.queryAllByRole('menuitemradio')).toHaveLength(0)
  })

  it('offers "Remove from category" only while the session is filed', () => {
    createCategory('Work')
    setSessions([session()])

    renderItems(LIVE_ID)
    expect(screen.queryByText('Remove from category')).toBeNull()

    cleanup()
    moveSessionToCategory(DURABLE_ID, $sessionCategories.get()[0]!.id)
    renderItems(LIVE_ID)

    expect(screen.getByText('Remove from category')).toBeTruthy()
  })

  it('unfiles the session without deleting the category', () => {
    const work = createCategory('Work')
    moveSessionToCategory(DURABLE_ID, work.id)
    setSessions([session()])

    renderItems(LIVE_ID)
    fireEvent.click(screen.getByText('Remove from category'))

    expect($sessionCategories.get().find(c => c.id === work.id)?.sessionIds).toEqual([])
    expect($sessionCategories.get()).toHaveLength(1)
  })

  describe('"New category…"', () => {
    it('creates the category, files the session under its durable id, and opens rename', () => {
      setSessions([session()])

      renderItems(LIVE_ID)
      fireEvent.click(screen.getByText('New category…'))

      const [category] = $sessionCategories.get()
      expect(category?.name).toBe('New category')
      expect(category?.sessionIds).toEqual([DURABLE_ID])
      expect($editingCategoryId.get()).toBe(category?.id)
    })

    // Regression: rename mode used to be deferred with setTimeout(..., 0) to
    // dodge Radix's focus restore. That race is unwinnable (the restore fires
    // from FocusScope's unmount cleanup, whose timing depends on the exit
    // animation), so the write is synchronous now and the restore is
    // suppressed instead. Fake timers make a re-introduced deferral fail here.
    it('enters rename mode synchronously, without a deferral', () => {
      vi.useFakeTimers()

      try {
        setSessions([session()])
        renderItems(LIVE_ID)
        fireEvent.click(screen.getByText('New category…'))

        // No timer has run yet.
        expect($editingCategoryId.get()).toBe($sessionCategories.get()[0]?.id)
        expect($editingCategoryId.get()).not.toBeNull()
      } finally {
        vi.useRealTimers()
      }
    })

    it('arms the close-auto-focus suppression flag', () => {
      setSessions([session()])
      const suppressRef = renderItems(LIVE_ID, { current: false })

      expect(suppressRef.current).toBe(false)

      fireEvent.click(screen.getByText('New category…'))

      expect(suppressRef.current).toBe(true)
    })

    it('leaves the suppression flag alone for every other item', () => {
      createCategory('Work')
      setSessions([session()])
      const suppressRef = renderItems(LIVE_ID, { current: false })

      fireEvent.click(screen.getByRole('menuitemradio', { name: 'Work' }))

      expect(suppressRef.current).toBe(false)
    })
  })
})

// Radix restores focus from FocusScope's UNMOUNT cleanup, on a setTimeout(0).
// Selecting an item closes the menu, so these tests drive the real close and
// then let that timer run — no hand-rolled event, no assumption about ordering.
async function settleFocusRestore() {
  await waitFor(() => {
    expect(screen.queryByRole('menu')).toBeNull()
  })
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 0))
  })
}

/** A focusable sentinel standing in for whatever held focus before the menu
 *  opened (the composer) or took it during the close (the sidebar's rename
 *  input). Focused before the menu opens so FocusScope captures it as
 *  `previouslyFocusedElement`. */
function focusedSentinel() {
  const sentinel = document.createElement('input')

  document.body.append(sentinel)
  sentinel.focus()

  return sentinel
}

function openDropdown() {
  render(
    <SessionActionsMenu sessionId={LIVE_ID} title="A session">
      <button type="button">actions</button>
    </SessionActionsMenu>
  )

  const trigger = screen.getByRole('button', { name: 'actions' })

  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: 'mouse' })

  return trigger
}

function openContextMenu() {
  render(
    <SessionContextMenu sessionId={LIVE_ID} title="A session">
      <div>row</div>
    </SessionContextMenu>
  )

  fireEvent.contextMenu(screen.getByText('row'))
}

function openCategorySubmenu() {
  fireEvent.click(screen.getByRole('menuitem', { name: /move to category/i }))
}

describe('"Move to category" menu wiring', () => {
  beforeEach(() => {
    setSessions([session()])
  })

  // Deleting the <kit.Sub> block used to leave the whole suite green.
  it('renders the submenu trigger in the triple-dot dropdown', () => {
    openDropdown()

    expect(screen.getByRole('menuitem', { name: /move to category/i })).toBeTruthy()
  })

  it('renders the submenu trigger in the right-click context menu', () => {
    openContextMenu()

    expect(screen.getByRole('menuitem', { name: /move to category/i })).toBeTruthy()
  })

  // Regression for the focus steal: after "New category…" the sidebar's
  // autoFocus rename input owns focus, so the menu must NOT pull focus back to
  // its trigger as it closes — that blur runs commitRename, so the input
  // vanishes before the user can type and the category is stuck at the literal
  // default name. The dropdown's trigger (unlike the category kebab) is still
  // mounted at that point, which is why this path steals focus and the
  // pre-existing Rename path never did.
  it('suppresses the dropdown trigger refocus after "New category…"', async () => {
    const sentinel = focusedSentinel()

    openDropdown()
    openCategorySubmenu()
    fireEvent.click(screen.getByText('New category…'))
    // Stand in for the sidebar rename input mounting and autofocusing.
    sentinel.focus()
    await settleFocusRestore()

    expect(document.activeElement).toBe(sentinel)
    expect(document.activeElement).not.toBe(screen.getByRole('button', { name: 'actions' }))
  })

  // The suppression is scoped to that one close: every other item must still
  // hand focus back to the trigger, which is what keyboard users rely on.
  it('still refocuses the dropdown trigger for an ordinary item', async () => {
    createCategory('Work')
    focusedSentinel()

    openDropdown()
    openCategorySubmenu()
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Work' }))
    await settleFocusRestore()

    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'actions' }))
  })

  // The right-click flavour is worse than the dropdown: on an item select
  // Radix does not preventDefault, so FocusScope's fallback returns focus to
  // whatever was focused before the right-click (typically the composer).
  it('suppresses the context menu focus restore after "New category…"', async () => {
    const sentinel = focusedSentinel()
    const elsewhere = focusedSentinel()

    openContextMenu()
    openCategorySubmenu()
    fireEvent.click(screen.getByText('New category…'))
    // Stand in for the sidebar rename input taking focus during the close.
    sentinel.focus()
    await settleFocusRestore()

    expect(document.activeElement).toBe(sentinel)
    expect(document.activeElement).not.toBe(elsewhere)
  })

  it('leaves the context menu focus restore alone for an ordinary item', async () => {
    createCategory('Work')
    const sentinel = focusedSentinel()
    const elsewhere = focusedSentinel()

    openContextMenu()
    openCategorySubmenu()
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Work' }))
    sentinel.focus()
    await settleFocusRestore()

    expect(document.activeElement).toBe(elsewhere)
  })
})
