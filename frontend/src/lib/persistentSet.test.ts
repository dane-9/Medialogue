// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), replace: vi.fn() }))
vi.mock('../api/modules/uiSelections', () => ({ uiSelectionsApi: api }))

import { usePersistentSet } from './persistentSet'

beforeEach(() => { api.replace.mockResolvedValue([]) })
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers() })

describe('usePersistentSet', () => {
  it('loads the stored set, saves changes after a pause, and prunes rows that are gone', async () => {
    api.get.mockResolvedValue(['a', 'b'])
    const { result } = renderHook(() => usePersistentSet('new-media-excluded'))
    await waitFor(() => expect([...result.current[0]].sort()).toEqual(['a', 'b']))
    expect(api.replace).not.toHaveBeenCalled()

    act(() => result.current[1]((current) => new Set(current).add('c')))
    await waitFor(() => expect(api.replace).toHaveBeenCalledWith('new-media-excluded', ['a', 'b', 'c']))

    act(() => result.current[2](['b', 'c', 'd']))
    expect([...result.current[0]]).toEqual(['b', 'c'])
    await waitFor(() => expect(api.replace).toHaveBeenLastCalledWith('new-media-excluded', ['b', 'c']))
  })

  it('keeps and saves a change made before the stored set arrived', async () => {
    let resolve: (ids: string[]) => void = () => undefined
    api.get.mockReturnValue(new Promise<string[]>((done) => { resolve = done }))
    const { result } = renderHook(() => usePersistentSet('upgrade-media-excluded'))
    act(() => result.current[1](new Set(['mine'])))
    await act(async () => { resolve(['old']) })
    expect([...result.current[0]]).toEqual(['mine'])
    await waitFor(() => expect(api.replace).toHaveBeenCalledWith('upgrade-media-excluded', ['mine']))
  })
})
