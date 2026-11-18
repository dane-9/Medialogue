// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { noteChange, useFreshLoad } from './freshness'
import { cachedList, clearListCache, rememberList } from './listCache'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('useFreshLoad', () => {
  it('drops a read that a newer read overtook', async () => {
    const { result } = renderHook(() => useFreshLoad())
    const applied: string[] = []
    const older = deferred<string>()
    const newer = deferred<string>()
    let first!: Promise<boolean>
    let second!: Promise<boolean>
    act(() => {
      first = result.current(() => older.promise, (value) => { applied.push(value) })
      second = result.current(() => newer.promise, (value) => { applied.push(value) })
    })
    await act(async () => { newer.resolve('new'); older.resolve('old'); await Promise.all([first, second]) })
    expect(applied).toEqual(['new'])
  })

  it('reads again when a change was made while it was loading', async () => {
    const { result } = renderHook(() => useFreshLoad())
    const applied: string[] = []
    const answers = ['before the change', 'after the change']
    let calls = 0
    await act(async () => {
      await result.current(async () => {
        const answer = answers[calls]
        calls += 1
        if (calls === 1) noteChange()
        return answer
      }, (value) => { applied.push(value) })
    })
    expect(applied).toEqual(['after the change'])
  })
})

describe('cachedList', () => {
  it('is not shown once something was changed after it was loaded', async () => {
    clearListCache()
    rememberList('newMedia', [1, 2])
    expect(cachedList('newMedia')).toEqual([1, 2])
    await new Promise((resolve) => { setTimeout(resolve, 5) })
    noteChange()
    expect(cachedList('newMedia')).toBeUndefined()
    rememberList('newMedia', [3])
    expect(cachedList('newMedia')).toEqual([3])
  })
})
