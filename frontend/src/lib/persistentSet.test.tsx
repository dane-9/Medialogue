// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const stored = { ids: ['a', 'b'] }
let resolveGet: (ids: string[]) => void = () => undefined
const replace = vi.fn(async (_list: string, ids: string[]) => ids)

vi.mock('../api/modules/uiSelections', () => ({
  uiSelectionsApi: {
    get: () => new Promise<string[]>((resolve) => { resolveGet = resolve }),
    replace: (list: string, ids: string[]) => replace(list, ids),
  },
}))

const { usePersistentSet } = await import('./persistentSet')

describe('usePersistentSet', () => {
  beforeEach(() => { replace.mockClear() })

  it('keeps the stored choices when a row is ticked before they arrive', async () => {
    const { result } = renderHook(() => usePersistentSet('upgrade-media-excluded'))
    act(() => { result.current[1]((current) => new Set(current).add('c')) })
    await act(async () => { resolveGet(stored.ids) })
    expect([...result.current[0]].sort()).toEqual(['a', 'b', 'c'])
  })

  it('saves a change at once when the page is left before the save delay', async () => {
    const { result, unmount } = renderHook(() => usePersistentSet('upgrade-media-excluded'))
    await act(async () => { resolveGet(stored.ids) })
    act(() => { result.current[1]((current) => new Set(current).add('d')) })
    expect(replace).not.toHaveBeenCalled()
    unmount()
    expect(replace).toHaveBeenCalledTimes(1)
    expect([...replace.mock.calls[0][1]].sort()).toEqual(['a', 'b', 'd'])
  })
})
