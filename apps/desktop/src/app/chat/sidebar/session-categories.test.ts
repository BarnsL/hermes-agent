import { beforeEach, describe, expect, it } from 'vitest'

import {
  $sessionCategories,
  createCategory,
  deleteCategory,
  moveSessionToCategory,
  removeSessionFromCategory,
  renameCategory,
  sanitizeCategories,
  toggleCategoryCollapsed
} from '@/store/layout'

describe('session categories store', () => {
  beforeEach(() => {
    $sessionCategories.set([])
  })

  it('creates a category with a unique id and empty membership', () => {
    const a = createCategory('Work')
    const b = createCategory('Play')

    expect(a.id).not.toBe(b.id)
    expect($sessionCategories.get()).toEqual([
      { id: a.id, name: 'Work', sessionIds: [] },
      { id: b.id, name: 'Play', sessionIds: [] }
    ])
  })

  it('renames only the targeted category', () => {
    const a = createCategory('Work')
    const b = createCategory('Play')

    renameCategory(a.id, 'Deep work')

    const cats = $sessionCategories.get()
    expect(cats.find(c => c.id === a.id)?.name).toBe('Deep work')
    expect(cats.find(c => c.id === b.id)?.name).toBe('Play')
  })

  it('deletes a category without touching its siblings', () => {
    const a = createCategory('Work')
    const b = createCategory('Play')

    deleteCategory(a.id)

    expect($sessionCategories.get().map(c => c.id)).toEqual([b.id])
  })

  it('toggles collapsed state back and forth', () => {
    const a = createCategory('Work')

    toggleCategoryCollapsed(a.id)
    expect($sessionCategories.get()[0]?.collapsed).toBe(true)

    toggleCategoryCollapsed(a.id)
    expect($sessionCategories.get()[0]?.collapsed).toBe(false)
  })

  it('moveSessionToCategory keeps a session in at most one category', () => {
    const a = createCategory('Work')
    const b = createCategory('Play')

    moveSessionToCategory('s1', a.id)
    moveSessionToCategory('s2', a.id)
    moveSessionToCategory('s1', b.id)

    const cats = $sessionCategories.get()
    expect(cats.find(c => c.id === a.id)?.sessionIds).toEqual(['s2'])
    expect(cats.find(c => c.id === b.id)?.sessionIds).toEqual(['s1'])
  })

  it('moveSessionToCategory does not duplicate an existing member', () => {
    const a = createCategory('Work')

    moveSessionToCategory('s1', a.id)
    moveSessionToCategory('s1', a.id)

    expect($sessionCategories.get()[0]?.sessionIds).toEqual(['s1'])
  })

  it('removeSessionFromCategory removes only from the targeted category', () => {
    const a = createCategory('Work')
    const b = createCategory('Play')

    moveSessionToCategory('s1', a.id)
    moveSessionToCategory('s2', b.id)
    removeSessionFromCategory('s2', b.id)

    const cats = $sessionCategories.get()
    expect(cats.find(c => c.id === a.id)?.sessionIds).toEqual(['s1'])
    expect(cats.find(c => c.id === b.id)?.sessionIds).toEqual([])
  })

  it('moveSessionToCategory is a no-op for an unknown target category', () => {
    const a = createCategory('Work')

    moveSessionToCategory('s1', a.id)
    // e.g. the target was deleted in another window between drag start and drop
    moveSessionToCategory('s1', 'cat_gone')

    expect($sessionCategories.get().find(c => c.id === a.id)?.sessionIds).toEqual(['s1'])
  })
})

describe('sanitizeCategories (persisted-shape codec)', () => {
  it('returns [] for non-array input', () => {
    expect(sanitizeCategories(null)).toEqual([])
    expect(sanitizeCategories('nope')).toEqual([])
    expect(sanitizeCategories({ id: 'a' })).toEqual([])
  })

  it('drops malformed entries and scrubs non-string session ids', () => {
    const result = sanitizeCategories([
      null,
      42,
      { name: 1 },
      { id: '', name: 'empty id' },
      { id: 'a', name: 'ok', sessionIds: [3, 's1', null, 's2'] },
      { id: 'b', name: 'no ids', sessionIds: 'not-an-array' },
      { id: 'c', name: 'collapsed junk', sessionIds: [], collapsed: 'yes' }
    ])

    expect(result).toEqual([
      { id: 'a', name: 'ok', sessionIds: ['s1', 's2'] },
      { id: 'b', name: 'no ids', sessionIds: [] },
      { id: 'c', name: 'collapsed junk', sessionIds: [] }
    ])
  })

  it('preserves a well-formed entry, including collapsed: true', () => {
    expect(sanitizeCategories([{ collapsed: true, id: 'a', name: 'Work', sessionIds: ['s1'] }])).toEqual([
      { collapsed: true, id: 'a', name: 'Work', sessionIds: ['s1'] }
    ])
  })
})
