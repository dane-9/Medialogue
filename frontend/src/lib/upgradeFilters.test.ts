import { describe, expect, it } from 'vitest'
import { filterUpgrades, upgradeFilterSections } from './upgradeFilters'
import type { UpgradeCandidate, UpgradeMatchedFormat } from '../types'

const format = (name: string, contribution: number, group?: string): UpgradeMatchedFormat => ({ id: name, name, contribution, group })

const candidate = (id: string, overrides: Partial<UpgradeCandidate> = {}) => ({
  id,
  indexerName: 'PTP',
  quality: '2160p BluRay REMUX',
  seeders: 10,
  upgradeTypes: [],
  currentMatchedFormats: [],
  candidateMatchedFormats: [],
  ...overrides,
}) as UpgradeCandidate

const ids = (items: UpgradeCandidate[]) => items.map((item) => item.id)
const section = (items: UpgradeCandidate[], key: string, filters = {}) => upgradeFilterSections(items, filters).find((entry) => entry.key === key)

describe('what an upgrade improves', () => {
  const improves = (item: UpgradeCandidate) => section([item], 'improves')?.options.map((option) => option.value) ?? []

  it('reads quality, re-release and season pack from the upgrade types', () => {
    expect(improves(candidate('a', { upgradeTypes: ['quality', 'proper', 'season_pack'] }))).toEqual(['Quality', 'Repack/Proper', 'Season pack'])
  })

  it('judges a group on its total, like the score hover', () => {
    expect(improves(candidate('a', {
      currentMatchedFormats: [format('DTS-HD MA', 50, 'Audio')],
      candidateMatchedFormats: [format('TrueHD Atmos', 60, 'Audio'), format('DV', 40, 'Dynamic Range')],
    }))).toEqual(['Audio', 'Dynamic Range'])
    expect(improves(candidate('b', {
      currentMatchedFormats: [format('TrueHD', 60, 'Audio')],
      candidateMatchedFormats: [format('DTS', 30, 'Audio')],
    }))).toEqual([])
  })

  it('never counts a better release group as an improvement', () => {
    expect(improves(candidate('a', { candidateMatchedFormats: [format('Remux Tier 01', 40, 'Release Group')] }))).toEqual([])
  })

  it('files anything else under other formats', () => {
    expect(improves(candidate('a', { candidateMatchedFormats: [format('Mine', 5), format('AMZN', 5, 'Streaming')] }))).toEqual(['Other formats'])
  })
})

describe('filterUpgrades', () => {
  const remux = candidate('remux')
  const web = candidate('web', { quality: '2160p WEB-DL', indexerName: 'HDB', seeders: 2 })
  const hd = candidate('hd', { quality: '1080p BluRay REMUX', seeders: undefined })
  const items = [remux, web, hd]

  it('shows everything with nothing picked', () => {
    expect(ids(filterUpgrades(items, {}))).toEqual(['remux', 'web', 'hd'])
  })

  it('matches any pick within a section and every section picked', () => {
    expect(ids(filterUpgrades(items, { source: ['REMUX', 'WEB-DL'] }))).toEqual(['remux', 'web', 'hd'])
    expect(ids(filterUpgrades(items, { resolution: ['2160p'], source: ['REMUX'] }))).toEqual(['remux'])
  })

  it('ignores a remembered pick no row here could satisfy', () => {
    expect(ids(filterUpgrades(items, { indexer: ['Gone'] }))).toEqual(['remux', 'web', 'hd'])
  })

  it('rates seeds as the row does', () => {
    expect(ids(filterUpgrades(items, { seeds: ['Low seeds'] }))).toEqual(['web'])
    expect(ids(filterUpgrades([...items, candidate('dead', { seeders: 0 })], { seeds: ['Low seeds'] }))).toEqual(['web', 'dead'])
    expect(ids(filterUpgrades(items, { seeds: ['Unknown'] }))).toEqual(['hd'])
  })

  it('tells a remux from the same source encoded or not', () => {
    const plain = candidate('plain', { quality: '1080p BluRay' })
    const options = section([remux, plain], 'source')?.options.map((option) => option.value)
    expect(options).toEqual(['REMUX', 'BluRay'])
  })
})

describe('upgradeFilterSections', () => {
  const items = [
    candidate('a', { quality: '1080p WEB-DL' }),
    candidate('b', { quality: '2160p WEB-DL', indexerName: 'HDB' }),
    candidate('c', { quality: '2160p BluRay REMUX', indexerName: 'HDB' }),
  ]

  it('hides a section no row has anything for', () => {
    expect(section(items, 'improves')).toBeUndefined()
  })

  it('puts the highest resolution first', () => {
    expect(section(items, 'resolution')?.options.map((option) => option.value)).toEqual(['2160p', '1080p'])
  })

  it('counts what picking an option would show', () => {
    const filters = { resolution: ['2160p'] }
    // The other sections narrow to 2160p; its own section still offers 1080p.
    expect(section(items, 'source', filters)?.options).toEqual([
      { value: 'REMUX', count: 1, on: false },
      { value: 'WEB-DL', count: 1, on: false },
    ])
    expect(section(items, 'resolution', filters)?.options).toEqual([
      { value: '2160p', count: 2, on: true },
      { value: '1080p', count: 1, on: false },
    ])
  })
})
