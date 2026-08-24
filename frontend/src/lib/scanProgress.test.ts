import { describe, expect, it } from 'vitest'
import { describeScan } from './scanProgress'
import type { StorageRootScan } from '../types'

const scan = (over: Partial<StorageRootScan> = {}): StorageRootScan => ({
  job_id: 'job-1', status: 'running', stage: 'reconciling', current: 12, total: 48, percent: 25, ...over,
})

describe('describeScan', () => {
  it('reports the counts and the percentage', () => {
    expect(describeScan(scan())).toBe('12 of 48 folders (25%)')
  })

  it('reads the same whatever stage the scan is in', () => {
    expect(describeScan(scan({ stage: 'enumerating', total: 10, current: 3, percent: 30 })))
      .toBe('3 of 10 folders (30%)')
  })

  it('avoids a meaningless percentage before anything has been counted', () => {
    expect(describeScan(scan({ stage: 'enumerating', current: 0, total: 0, percent: 0 })))
      .toBe('Nothing counted yet')
  })

  it('says a queued scan is waiting rather than running', () => {
    expect(describeScan(scan({ status: 'queued' }))).toBe('Waiting for another scan to finish')
  })

  it('ignores an unknown stage', () => {
    expect(describeScan(scan({ stage: 'something-new' }))).toBe('12 of 48 folders (25%)')
  })

  it('rounds a fractional percentage', () => {
    expect(describeScan(scan({ percent: 25.4 }))).toContain('(25%)')
  })

  it('clamps a percentage that overshoots', () => {
    expect(describeScan(scan({ percent: 140 }))).toContain('(100%)')
  })

  it('describes a missing scan without throwing', () => {
    expect(describeScan(undefined)).toBe('Scanning')
  })
})
