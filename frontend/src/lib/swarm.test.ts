import { describe, expect, it } from 'vitest'
import { formatPeers, HEALTHY_SEEDERS, swarmHealth } from './swarm'

describe('swarmHealth', () => {
  it('grades a well-seeded release healthy', () => {
    expect(swarmHealth(200)).toBe('healthy')
    expect(swarmHealth(HEALTHY_SEEDERS)).toBe('healthy')
  })

  it('grades a thin swarm weak', () => {
    expect(swarmHealth(HEALTHY_SEEDERS - 1)).toBe('weak')
    expect(swarmHealth(1)).toBe('weak')
  })

  it('grades an unseeded release dead', () => {
    expect(swarmHealth(0)).toBe('dead')
  })

  it('does not confuse a missing count with zero seeders', () => {
    // The difference is real: unknown might download fine, dead cannot.
    expect(swarmHealth(undefined)).toBe('unknown')
    expect(swarmHealth(null)).toBe('unknown')
    expect(swarmHealth(0)).toBe('dead')
  })
})

describe('formatPeers', () => {
  it('pairs seeders with leechers', () => {
    expect(formatPeers(20, 1)).toBe('20/1')
  })

  it('shows a dash rather than claiming zero for a count it does not have', () => {
    expect(formatPeers(20, undefined)).toBe('20/—')
    expect(formatPeers(undefined, undefined)).toBe('—/—')
  })

  it('keeps a genuine zero distinct from a missing count', () => {
    expect(formatPeers(0, 0)).toBe('0/0')
  })
})
