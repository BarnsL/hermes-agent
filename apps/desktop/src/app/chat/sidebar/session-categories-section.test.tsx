import { useStore } from '@nanostores/react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/hermes'
import { $editingCategoryId, $sessionCategories, createCategory, setEditingCategoryId } from '@/store/layout'

import { SessionCategoriesSection } from './session-categories-section'

// Rename mode can be entered from OUTSIDE CategorySection — the session actions
// menu's "Move to category" → "New category…" writes $editingCategoryId
// directly — so the input seeds itself (and re-arms its commit guard) from a
// layout effect rather than only inside beginRename. Reverting that hunk leaves
// an EMPTY autofocused input whose blur discards the name, and every store-only
// test still passes. These tests are what stands between that and a regression.

vi.mock('@/hermes', () => ({
  renameSession: async () => ({ ok: true }),
  // profile.ts calls this at import (its $activeGatewayProfile subscribe fires
  // immediately), pulled in transitively via session-states.
  setApiRequestProfile: () => {},
  HermesGateway: class {}
}))

vi.mock('@/store/gateway', () => ({
  activeGateway: () => null
}))

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', TestResizeObserver)
  Element.prototype.scrollIntoView ??= () => undefined
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.setPointerCapture ??= () => undefined
  Element.prototype.releasePointerCapture ??= () => undefined
})

beforeEach(() => {
  $sessionCategories.set([])
  setEditingCategoryId(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderSection() {
  const view = render(<Harness />)

  return view
}

// Mirrors ChatSidebar: the store subscriptions live in the parent, which passes
// `categories` and `editingCategoryId` down as props.
function Harness() {
  const categories = useSyncedCategories()

  return (
    <SessionCategoriesSection
      activeSessionId={null}
      categories={categories.list}
      editingCategoryId={categories.editingId}
      onArchiveSession={() => {}}
      onBranchSession={() => {}}
      onDeleteSession={() => {}}
      onResumeSession={() => {}}
      sessionById={new Map<string, SessionInfo>()}
      workingSessionIdSet={new Set<string>()}
    />
  )
}

function useSyncedCategories() {
  return { editingId: useStore($editingCategoryId), list: useStore($sessionCategories) }
}

describe('CategorySection rename input', () => {
  it('seeds the input with the current name when rename mode is entered from outside', () => {
    const work = createCategory('Work')
    renderSection()

    // Nothing inside the component put it into rename mode — this is exactly
    // what the "New category…" menu item does.
    act(() => setEditingCategoryId(work.id))

    expect(screen.getByRole('textbox')).toHaveProperty('value', 'Work')
  })

  it('commits an externally-entered rename on blur', () => {
    const work = createCategory('Work')
    renderSection()
    act(() => setEditingCategoryId(work.id))

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Research' } })
    fireEvent.blur(screen.getByRole('textbox'))

    expect($sessionCategories.get()[0]?.name).toBe('Research')
    expect($editingCategoryId.get()).toBeNull()
  })

  it('discards the edit on Escape', () => {
    const work = createCategory('Work')
    renderSection()
    act(() => setEditingCategoryId(work.id))

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Discarded' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })

    expect($sessionCategories.get()[0]?.name).toBe('Work')
    expect($editingCategoryId.get()).toBeNull()
  })

  it('re-arms the commit guard so a second rename on the same instance still commits', () => {
    const work = createCategory('Work')
    renderSection()

    act(() => setEditingCategoryId(work.id))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'First' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    expect($sessionCategories.get()[0]?.name).toBe('First')

    // committedRef is left true by the commit above; without the re-arm this
    // second rename silently no-ops.
    act(() => setEditingCategoryId(work.id))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Second' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

    expect($sessionCategories.get()[0]?.name).toBe('Second')
  })

  it('lands the "+" button straight in rename mode', () => {
    renderSection()

    fireEvent.click(screen.getByRole('button', { name: 'New category' }))

    const [category] = $sessionCategories.get()
    expect(category).toBeTruthy()
    expect($editingCategoryId.get()).toBe(category?.id)
    expect(screen.getByRole('textbox')).toHaveProperty('value', category?.name)
  })
})
