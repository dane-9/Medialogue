

type SwarmHealth = 'dead' | 'weak' | 'healthy' | 'unknown'

export const HEALTHY_SEEDERS = 5

export function swarmHealth(seeders?: number | null): SwarmHealth {
  if (seeders === undefined || seeders === null) return 'unknown'
  if (seeders <= 0) return 'dead'
  return seeders < HEALTHY_SEEDERS ? 'weak' : 'healthy'
}

export function formatPeers(seeders?: number | null, leechers?: number | null): string {
  const seed = seeders === undefined || seeders === null ? '-' : String(seeders)
  const leech = leechers === undefined || leechers === null ? '-' : String(leechers)
  return `${seed}/${leech}`
}
