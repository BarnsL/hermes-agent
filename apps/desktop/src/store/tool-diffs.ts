import { atom, computed, type ReadableAtom } from 'nanostores'

const $toolDiffs = atom<Record<string, string>>({})

export function recordToolDiff(toolCallId: string, diff: string) {
  if (!toolCallId || !diff) {
    return
  }

  const current = $toolDiffs.get()

  if (current[toolCallId] === diff) {
    return
  }

  $toolDiffs.set({ ...current, [toolCallId]: diff })
}

export function getToolDiff(toolCallId: string): string {
  return toolCallId ? $toolDiffs.get()[toolCallId] || '' : ''
}

// Per-id accessor (same pattern as $toolRowDismissed / $toolDisclosureOpen):
// recordToolDiff replaces the whole map on every file-edit completion, so a
// row subscribing to the map re-renders for every OTHER row's diff too. The
// computed only notifies when this id's own diff string changes.
const EMPTY_DIFF = atom('')

const diffCache = new Map<string, ReadableAtom<string>>()

export function $toolInlineDiff(id: string): ReadableAtom<string> {
  if (!id) {
    return EMPTY_DIFF
  }

  let cached = diffCache.get(id)

  if (!cached) {
    cached = computed($toolDiffs, diffs => diffs[id] || '')
    diffCache.set(id, cached)
  }

  return cached
}
