import { PageTopbar } from "./components/Shell"
import { useEffect, useState } from 'react'
import { openApiClient, openApiData } from './api/transport'
import { upgradesApi } from './api/modules/upgrades'
import { integrationsApi } from './api/modules/integrations'
import { systemApi } from './api/modules/system'
import { Badge, Button, Panel, Select } from './components/ui'
import { useConfirm } from './components/Confirm'
import type { DownloadClient, UpgradeSearchResults } from './types'

type Item = {
  target_key: string; title: string; torrent_ids: string[]; reason: string; dismissed: boolean;
  media_type?: 'movies' | 'shows'; current_release?: string; current_quality?: string; current_score?: number;
  candidate_id?: string; candidate_title?: string; candidate_quality?: string; candidate_score?: number;
  seeders?: number; indexer?: string; execution_state?: string; error?: string; blocked_reason?: string;
  media_present?: boolean; last_search_result?: string; alternate_source?: boolean;
}

export default function ReplaceMediaPage() {
  const [items, setItems] = useState<Item[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [selectedClient, setSelectedClient] = useState('')
  const [showDismissed, setShowDismissed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [review, setReview] = useState<Item | null>(null)
  const [results, setResults] = useState<UpgradeSearchResults | null>(null)
  const confirm = useConfirm()
  const load = async () => {
    const [queue, configured] = await Promise.all([
      openApiData(openApiClient.GET('/api/v1/replacements')), integrationsApi.downloadClients(),
    ])
    setItems((queue as unknown as { items: Item[] }).items); setClients(configured); setLoading(false)
  }
  useEffect(() => { void load().catch(e => { setError(String(e)); setLoading(false) }) }, [])
  const act = async (fn: () => Promise<void>) => {
    setBusy(true); setError(''); setMessage('')
    try { await fn(); await load() } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const search = (item?: Item) => act(async () => {
    const result = await openApiData(openApiClient.POST('/api/v1/replacements/search', { body: { target_key: item?.target_key } }))
    setMessage('Searching for equivalent or better releases…')
    const [job] = await systemApi.waitForJobs([result.job_id])
    if (job?.state !== 'completed') throw new Error('Replacement search did not complete. Review Tasks for details.')
    setMessage(String(job.summary?.message || 'Search complete.'))
  })
  const beginReview = (item: Item) => act(async () => {
    setReview(item)
    const eligible = clients.filter(c => c.enabled && c.scope === item.media_type)
    setSelectedClient(eligible.length === 1 ? eligible[0].id : '')
    setResults(await upgradesApi.upgradeSearchResults(item.candidate_id!))
  })
  const decision = (item: Item, action: 'ignore' | 'snooze' | 'restore') => act(async () => {
    await openApiData(openApiClient.POST('/api/v1/replacements/review', { body: { torrent_ids: item.torrent_ids, action } }))
  })
  const cleanup = (item: Item) => act(async () => {
    const preview = await upgradesApi.upgradeCleanupPreview(item.candidate_id!)
    if (!preview.eligible) throw new Error(preview.blocked.join(' '))
    if (!await confirm({ title: 'Delete superseded media files?', body: <div><p>The replacement has been validated. These old paths will be deleted:</p><ul>{preview.targets.map(t => <li key={t.path}>{t.path}</li>)}</ul></div>, confirmLabel: 'Delete listed files', tone: 'danger' })) return
    await upgradesApi.cleanupUpgrade(item.candidate_id!, preview.confirmationToken)
    setMessage('Superseded media cleanup completed. Torrent archives are retained.')
  })
  return <div className="page"><PageTopbar title="Replace Media" subtitle="Find a new recovery source for confirmed tracker removals, with equivalent or better quality and CF score." action={<Button disabled={busy} onClick={() => void search()}>Search replacements</Button>} />
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <div className="button-row"><Button disabled={busy} onClick={() => void act(load)}>Refresh</Button><label><input type="checkbox" checked={showDismissed} onChange={e => setShowDismissed(e.target.checked)} /> Show ignored and snoozed items</label></div>
    {review && <Panel title={`Review replacement for ${review.title}`}>
      <p>Current: {review.current_quality ?? 'Unknown quality'} · CF {review.current_score ?? 'Unknown'}</p>
      <p>Selected: <strong>{review.candidate_title}</strong> · CF {review.candidate_score}</p>
      <p>The old torrent and media remain until the replacement completes and passes validation. Cleanup is a separate action.</p>
      <label>Download client<Select value={selectedClient} onChange={e => setSelectedClient(e.target.value)}><option value="">Select client…</option>{clients.filter(c => c.enabled && c.scope === review.media_type).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></label>
      <div className="button-row"><Button onClick={() => { setReview(null); setResults(null) }}>Cancel</Button><Button variant="primary" disabled={busy || !selectedClient} onClick={() => void act(async () => {
        if (!await confirm({ title: 'Download this replacement?', body: `${review.candidate_title} will be submitted to the selected qBittorrent client.`, confirmLabel: 'Download replacement', tone: 'primary' })) return
        await upgradesApi.executeUpgrade(review.candidate_id!, selectedClient); setReview(null); setResults(null); setMessage('Replacement submitted. Follow its progress in Downloads or refresh this queue.')
      })}>Download replacement</Button></div>
      {results && <table className="data-table"><thead><tr><th>Available release</th><th>Source</th><th>CF score</th><th>Reported seeders</th><th /></tr></thead><tbody>{results.results.map(result => <tr key={result.id}>
        <td>{result.title}{!result.eligible && <p>{result.rejectionReasons.join(' ')}</p>}<small>{result.matchedFormatNames.join(', ')}</small></td><td>{result.indexerName}</td><td>{result.customFormatScore ?? 'Unknown'}</td><td>{result.seeders ?? 'Unknown'}</td>
        <td><Button disabled={busy || !result.eligible} onClick={() => void act(async () => {
          const value = await upgradesApi.selectUpgradeSearchResult(review.candidate_id!, result.id)
          setReview({ ...review, candidate_title: value.title, candidate_score: value.customFormatScore }); setResults(await upgradesApi.upgradeSearchResults(review.candidate_id!))
        })}>{result.selected ? 'Selected' : 'Choose'}</Button></td>
      </tr>)}</tbody></table>}
    </Panel>}
    {loading ? <p>Checking recorded tracker evidence…</p> : <Panel title="Recovery sources needing attention">
      {!items.some(item => showDismissed || !item.dismissed) && <p>No confirmed source removals need attention. Archived-only torrents have unknown current availability.</p>}
      {items.filter(item => showDismissed || !item.dismissed).map(item => <div className="settings-section" key={item.target_key}>
        <div><h3>{item.title}</h3><p>{item.reason}{item.media_present === true ? ' · Media present' : item.media_present === false ? ' · Media missing or incomplete' : ''}{item.alternate_source ? ' · Another associated source is working' : ''}</p>
          {item.current_release && <p>{item.current_release} · {item.current_quality} · CF {item.current_score}</p>}
          {item.candidate_title ? <p>Replacement: {item.candidate_title} · CF {item.candidate_score}{item.indexer ? ` · ${item.indexer}` : ''}</p> : <p>{item.blocked_reason || (item.last_search_result === 'no_match' ? 'No suitable replacement found. Search again later or review the media profile.' : 'Search to find an equivalent or better replacement.')}</p>}
          {item.error && <p role="alert">{item.error}</p>}
        </div>
        <div className="button-row">
          {item.execution_state && !['failed', 'completed'].includes(item.execution_state) ? <><Badge tone={item.execution_state === 'cleanup_required' ? 'green' : 'amber'}>{item.execution_state === 'cleanup_required' ? 'Replacement validated' : item.execution_state.replaceAll('_', ' ')}</Badge>
            {item.execution_state === 'handoff_attention' && <Button disabled={busy} onClick={() => void act(async () => { await upgradesApi.retryUpgradeHandoff(item.candidate_id!) })}>Retry validation</Button>}
            {item.execution_state === 'cleanup_required' && <><Button disabled={busy} onClick={() => void act(async () => {
              if (!await confirm({ title: 'Retire old torrent entries?', body: 'Remove superseded torrents from qBittorrent while keeping downloaded files and the archive.', confirmLabel: 'Retire torrent entries', tone: 'primary' })) return
              const response = await openApiData(openApiClient.POST('/api/v1/replacements/{candidate_id}/retire-torrents', { params: { path: { candidate_id: item.candidate_id! } } })) as { error?: string }
              if (response.error) throw new Error(response.error)
            })}>Retire old torrents</Button><Button disabled={busy} onClick={() => void cleanup(item)}>Review file cleanup</Button><Button disabled={busy} onClick={() => void act(async () => {
              await openApiData(openApiClient.POST('/api/v1/replacements/{candidate_id}/finish', { params: { path: { candidate_id: item.candidate_id! } } }))
              setMessage('Replacement finished. Old torrent entries, media, and archives were kept.')
            })}>Keep old media and finish</Button></>}
          </> : <>
            {!item.blocked_reason && <Button disabled={busy} onClick={() => void search(item)}>Search</Button>}
            {item.candidate_id && <Button disabled={busy} variant="primary" onClick={() => void beginReview(item)}>Review replacement</Button>}
            {!!item.torrent_ids.length && (item.dismissed ? <Button disabled={busy} onClick={() => void decision(item, 'restore')}>Return to queue</Button> : <><Button disabled={busy} onClick={() => void decision(item, 'snooze')}>Snooze 7 days</Button><Button disabled={busy} onClick={() => void decision(item, 'ignore')}>Ignore</Button></>)}
          </>}
        </div>
      </div>)}
    </Panel>}
  </div>
}
