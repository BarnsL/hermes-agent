import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $sessionCategories, createCategory, moveSessionToCategory, removeSessionFromCategory } from '@/store/layout'
import { $activeSessionId, $selectedStoredSessionId } from '@/store/session'

import { categoryForSession, createCategoryForSession, renameSessionPreferringRpc } from './session-actions-menu'

// The branched-session rename bug: a freshly branched session lives only in the
// gateway's runtime _sessions map (no state.db row yet), so REST PATCH
// /api/sessions/{id} 404s with "Session not found". renameSessionPreferringRpc
// must route the ACTIVE row through the session.title RPC (runtime id), which
// persists the row on demand, and otherwise fall back to REST.

const renameSession = vi.fn(async () => ({ ok: true, title: 'rest-title' }))
const request = vi.fn(async () => ({ title: 'rpc-title' }) as never)
const activeGateway = vi.fn<() => { request: typeof request } | null>(() => ({ request }))

vi.mock('@/hermes', () => ({
  renameSession: (...args: unknown[]) => renameSession(...(args as [])),
  // profile.ts calls this at import (its $activeGatewayProfile subscribe fires
  // immediately), pulled in transitively via session-states.
  setApiRequestProfile: () => {},
  HermesGateway: class {}
}))

vi.mock('@/store/gateway', () => ({
  activeGateway: () => activeGateway()
}))

const RUNTIME_ID = 'rt-runtime-1'
const STORED_ID = 'stored-branch-1'

afterEach(() => {
  renameSession.mockClear()
  request.mockClear()
  activeGateway.mockReset()
  activeGateway.mockReturnValue({ request })
  $activeSessionId.set(null)
  $selectedStoredSessionId.set(null)
})

describe('renameSessionPreferringRpc', () => {
  it('renames the active branched session via the session.title RPC, not REST', async () => {
    $selectedStoredSessionId.set(STORED_ID)
    $activeSessionId.set(RUNTIME_ID)

    const result = await renameSessionPreferringRpc(STORED_ID, 'My branch')

    expect(request).toHaveBeenCalledWith('session.title', { session_id: RUNTIME_ID, title: 'My branch' })
    expect(renameSession).not.toHaveBeenCalled()
    expect(result.title).toBe('rpc-title')
  })

  it('falls back to REST when the RPC fails (e.g. socket mid-reconnect)', async () => {
    $selectedStoredSessionId.set(STORED_ID)
    $activeSessionId.set(RUNTIME_ID)
    request.mockRejectedValueOnce(new Error('not connected'))

    const result = await renameSessionPreferringRpc(STORED_ID, 'My branch', 'work')

    expect(request).toHaveBeenCalledOnce()
    expect(renameSession).toHaveBeenCalledWith(STORED_ID, 'My branch', 'work')
    expect(result.title).toBe('rest-title')
  })

  it('uses REST for a non-active row (background/persisted session)', async () => {
    $selectedStoredSessionId.set('some-other-active-session')
    $activeSessionId.set(RUNTIME_ID)

    await renameSessionPreferringRpc(STORED_ID, 'My branch', 'work')

    expect(request).not.toHaveBeenCalled()
    expect(renameSession).toHaveBeenCalledWith(STORED_ID, 'My branch', 'work')
  })

  it('uses REST when clearing the title (RPC rejects empty titles)', async () => {
    $selectedStoredSessionId.set(STORED_ID)
    $activeSessionId.set(RUNTIME_ID)

    await renameSessionPreferringRpc(STORED_ID, '')

    expect(request).not.toHaveBeenCalled()
    expect(renameSession).toHaveBeenCalledWith(STORED_ID, '', undefined)
  })

  it('uses REST when no gateway is connected', async () => {
    $selectedStoredSessionId.set(STORED_ID)
    $activeSessionId.set(RUNTIME_ID)
    activeGateway.mockReturnValue(null)

    await renameSessionPreferringRpc(STORED_ID, 'My branch')

    expect(request).not.toHaveBeenCalled()
    expect(renameSession).toHaveBeenCalledWith(STORED_ID, 'My branch', undefined)
  })
})

// The "Move to category" submenu's logic, extracted so it is testable without
// mounting Radix. Both helpers take the DURABLE session id — the submenu
// resolves live → durable via sessionPinId before calling them, because
// membership keyed on the live id would evaporate at the next auto-compression.
describe('move-to-category menu helpers', () => {
  const DURABLE_ID = 'durable-session-1'

  beforeEach(() => {
    $sessionCategories.set([])
  })

  it('reports no current category when none exist at all', () => {
    expect(categoryForSession($sessionCategories.get(), DURABLE_ID)).toBeUndefined()
  })

  it('reports no current category for an uncategorized session', () => {
    const work = createCategory('Work')
    moveSessionToCategory('some-other-session', work.id)

    expect(categoryForSession($sessionCategories.get(), DURABLE_ID)).toBeUndefined()
  })

  it('reports the category a session was moved into', () => {
    createCategory('Work')
    const play = createCategory('Play')
    moveSessionToCategory(DURABLE_ID, play.id)

    expect(categoryForSession($sessionCategories.get(), DURABLE_ID)?.id).toBe(play.id)
  })

  it('creates a category and files the session into it in one action', () => {
    const created = createCategoryForSession(DURABLE_ID, 'Research')

    expect($sessionCategories.get()).toEqual([{ id: created.id, name: 'Research', sessionIds: [DURABLE_ID] }])
    expect(categoryForSession($sessionCategories.get(), DURABLE_ID)?.id).toBe(created.id)
  })

  it('keeps the session in at most one category when created into a new one', () => {
    const work = createCategory('Work')
    moveSessionToCategory(DURABLE_ID, work.id)

    const created = createCategoryForSession(DURABLE_ID, 'Research')

    const categories = $sessionCategories.get()
    expect(categories.find(c => c.id === work.id)?.sessionIds).toEqual([])
    expect(categories.find(c => c.id === created.id)?.sessionIds).toEqual([DURABLE_ID])
    expect(categories.filter(c => c.sessionIds.includes(DURABLE_ID))).toHaveLength(1)
  })

  it('trims the category name', () => {
    const created = createCategoryForSession(DURABLE_ID, '  Research  ')

    expect(created.name).toBe('Research')
  })

  it('never creates a blank or whitespace-only category name', () => {
    // The store's sanitizer accepts an empty name, so the guard has to live here.
    for (const blank of ['', '   ', '\t\n']) {
      $sessionCategories.set([])

      const created = createCategoryForSession(DURABLE_ID, blank)

      expect(created.name.trim()).not.toBe('')
      expect($sessionCategories.get()[0]?.name.trim()).not.toBe('')
    }
  })

  it('allows duplicate names and still resolves membership by id', () => {
    const first = createCategoryForSession('other-session', 'Work')
    const second = createCategoryForSession(DURABLE_ID, 'Work')

    expect(first.id).not.toBe(second.id)
    expect($sessionCategories.get().map(c => c.name)).toEqual(['Work', 'Work'])
    expect(categoryForSession($sessionCategories.get(), DURABLE_ID)?.id).toBe(second.id)
  })

  it('leaves the session uncategorized after removing it from its category', () => {
    const created = createCategoryForSession(DURABLE_ID, 'Research')
    // Assert the precondition, or this test would pass just as happily if the
    // session had never been filed in the first place.
    expect(categoryForSession($sessionCategories.get(), DURABLE_ID)?.id).toBe(created.id)

    removeSessionFromCategory(DURABLE_ID, created.id)

    expect(categoryForSession($sessionCategories.get(), DURABLE_ID)).toBeUndefined()
    // The category itself survives — removing a member is not a delete.
    expect($sessionCategories.get().map(c => c.id)).toEqual([created.id])
  })
})
