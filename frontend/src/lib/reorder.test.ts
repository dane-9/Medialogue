import { describe, expect, it } from 'vitest'
import { moveItem } from './reorder'

const list = ['a', 'b', 'c', 'd']

describe('moveItem', () => {
  it('moves an item downwards to the requested position', () => {
    expect(moveItem(list, 0, 2)).toEqual(['b', 'c', 'a', 'd'])
  })

  it('moves an item upwards to the requested position', () => {
    expect(moveItem(list, 3, 1)).toEqual(['a', 'd', 'b', 'c'])
  })

  it('moves an item to the end', () => {
    expect(moveItem(list, 0, 3)).toEqual(['b', 'c', 'd', 'a'])
  })

  it('returns a copy when the position is unchanged', () => {
    const same = moveItem(list, 2, 2)
    expect(same).toEqual(list)
    expect(same).not.toBe(list)
  })

  it('clamps a target past the end instead of dropping the item', () => {
    expect(moveItem(list, 1, 99)).toEqual(['a', 'c', 'd', 'b'])
  })

  it('ignores an out-of-range source', () => {
    expect(moveItem(list, 9, 0)).toEqual(list)
  })

  it('leaves a single-item list alone', () => {
    expect(moveItem(['only'], 0, 1)).toEqual(['only'])
  })
})
