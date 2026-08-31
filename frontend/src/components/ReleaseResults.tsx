import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { InteractiveSearchResult } from '../types'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'
import { formatPeers, swarmHealth } from '../lib/swarm'
import { formatAge } from '../lib/age'
import { languageLabel, releaseLanguages, UNKNOWN_LANGUAGE } from '../lib/languages'
import { parsedResolution, splitQuality } from '../lib/quality'
import { badgeColor } from '../lib/badgeColor'
import { DEFAULT_DESC, DEFAULT_SORT, sortReleases, type SortKey } from '../lib/releaseSort'

export function formatBytes(bytes?: number) {
  if (!bytes) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}

export function matchedFormatNames(result: InteractiveSearchResult): string[] {
  const raw = result.customFormatSnapshot.matched_format_names ?? result.customFormatSnapshot.matchedFormatNames
  return Array.isArray(raw) ? raw.map(String).filter(Boolean) : []
}

const signed = (score?: number) => `${(score ?? 0) > 0 ? '+' : ''}${score ?? 0}`

export function ReleaseResults({ results, selectedId, onSelect, disabled, empty, label, tag, onRefresh, refreshing }: {
  results: InteractiveSearchResult[]
  selectedId: string
  onSelect: (id: string) => void
  disabled?: boolean
  empty: ReactNode
  label: string

  tag?: string

  onRefresh?: () => void
  refreshing?: boolean
}) {
  const [sort, setSort] = useState<SortKey>(DEFAULT_SORT)
  const [desc, setDesc] = useState(DEFAULT_DESC[DEFAULT_SORT])

  const sorted = useMemo(() => sortReleases(results, sort, desc), [results, sort, desc])

  const choose = (key: SortKey) => {
    if (key !== sort) { setSort(key); setDesc(DEFAULT_DESC[key]); return }
    if (desc === DEFAULT_DESC[key]) { setDesc(!DEFAULT_DESC[key]); return }
    setSort(DEFAULT_SORT)
    setDesc(DEFAULT_DESC[DEFAULT_SORT])
  }

  const header = (key: SortKey, text: string, centred = false) => <Tooltip
    asChild
    content={sort === key ? 'Click again to reverse, once more for the ranked order' : `Sort by ${text.toLowerCase()}`}
  >
    <button
      type="button"
      className={`release-sort ${centred ? 'release-centred' : ''} ${sort === key ? 'active' : ''}`}
      onClick={() => choose(key)}
    >{text}{sort === key ? <i>{desc ? '▾' : '▴'}</i> : null}</button>
  </Tooltip>

  const refreshButton = onRefresh
    ? <Tooltip asChild content="Search the indexers again">
        <button
          type="button"
          className="release-refresh"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Search the indexers again"
        ><Icon name="refresh" size={15} /></button>
      </Tooltip>
    : null

  if (!results.length) return <div className="release-list">
    {onRefresh ? <div className="release-head release-head-bare">{refreshButton}</div> : null}
    <div className="release-empty">{empty}</div>
  </div>

  return <div className="release-list" role="listbox" aria-label={label}>
    <div className="release-head">
      {header('quality', 'Format', true)}
      {header('title', 'Name')}
      {header('score', 'CF Score', true)}
      {header('size', 'Size', true)}
      {header('seeders', 'Peers', true)}
      {header('age', 'Age', true)}
      {refreshButton}
    </div>

    {sorted.map((result) => {
      const selected = selectedId === result.id
      const score = result.customFormatScore ?? 0
      const { resolution, type } = splitQuality(result.quality, parsedResolution(result.parser))
      const formats = matchedFormatNames(result)
      const warnings = result.warnings ?? []
      const scoreRejected = result.minimumCustomFormatScoreMet === false
      const languages = releaseLanguages(result.parser)
      const indexerBadge = badgeColor(result.indexerColor)

      return <button
        type="button"
        role="option"
        key={result.id}
        aria-selected={selected}
        disabled={disabled}
        className={`release-row ${selected ? 'selected' : ''}`}
        onClick={() => onSelect(selected ? '' : result.id)}
      >
        <Tooltip asChild content={result.quality || 'Unknown quality'}>
          <span className="release-quality">
            {resolution ? <strong>{resolution}</strong> : null}
            <small>{type || 'Unknown'}</small>
          </span>
        </Tooltip>
        <span className="release-name">
          <strong>{result.title}</strong>
          <span className="release-tags">
            <span
              className="release-pill release-pill-indexer"
              style={indexerBadge
                ? { color: indexerBadge.text, borderColor: indexerBadge.border, background: indexerBadge.background }
                : undefined}
            ><i>Indexer</i>{result.indexerName}</span>
            {result.edition ? <em>{result.edition}</em> : null}
            {languages.length > 1
              ? <Tooltip
                  className="release-pill release-pill-language"
                  content={<>
                    <ul>{languages.map((name) => <li key={name}>{name}</li>)}</ul>
                  </>}
                >{languageLabel(languages)}</Tooltip>
              : languages.length === 1
                ? <span className={`release-pill release-pill-language ${languages[0] === UNKNOWN_LANGUAGE ? 'release-pill-unknown' : ''}`}>{languages[0]}</span>
                : null}
            {tag ? <em>{tag}</em> : null}
            {warnings.length
              ? <Tooltip
                  className={`release-pill ${result.qualityAllowed && !scoreRejected ? 'release-warned' : 'release-blocked'}`}
                  content={<>
                    <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
                  </>}
                ><Icon name="alert" size={11} />{!result.qualityAllowed ? 'Blocked by profile' : scoreRejected ? 'Rejected by score' : warnings.length}</Tooltip>
              : null}
          </span>
        </span>
        <Tooltip
          className={`release-numeric release-score ${score < 0 ? 'negative' : score > 0 ? 'positive' : ''} ${formats.length ? 'has-formats' : ''}`}
          content={formats.length
            ? <>
                <ul>{formats.map((name) => <li key={name}>{name}</li>)}</ul>
              </>
            : 'No Custom Formats matched'}
        >{signed(score)}</Tooltip>
        <span className="release-numeric">{formatBytes(result.size)}</span>
        <span className="release-numeric">
          <Tooltip
            className={`release-pill release-pill-swarm swarm-${swarmHealth(result.seeders)}`}
            content={`${result.seeders ?? 'N/A'} seeders, ${result.leechers ?? 'N/A'} leechers`}
          >{formatPeers(result.seeders, result.leechers)}</Tooltip>
        </span>
        <Tooltip
          className="release-numeric release-age"
          content={result.publishedAt ? new Date(result.publishedAt).toLocaleString() : 'This indexer reported no publication date'}
        >{formatAge(result.publishedAt)}</Tooltip>
      </button>
    })}
  </div>
}
