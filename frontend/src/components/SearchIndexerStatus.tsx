import type { InteractiveSearchJob } from '../types'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'

const FINISHED = ['completed', 'failed', 'cancelled', 'interrupted']

/** One chip per indexer in a search, so it is clear which ones are still being waited on.
 *
 * Results stream in as each indexer answers, so a list that looks settled may
 * still grow; the chips show which indexers have answered, with how many
 * results, and which failed and why.
 */
export function SearchIndexerStatus({ job }: { job: InteractiveSearchJob | null | undefined }) {
  if (!job?.indexers.length) return null
  const finished = FINISHED.includes(job.status)
  const waiting = job.indexers.filter((indexer) => indexer.status === 'queued').length
  return <div className="search-indexers" role="status" aria-live="polite">
    <span className="search-indexers-summary">{finished
      ? `Searched ${job.indexers.length - waiting} of ${job.indexers.length} indexer${job.indexers.length === 1 ? '' : 's'}`
      : `Waiting on ${waiting} of ${job.indexers.length} indexer${job.indexers.length === 1 ? '' : 's'}`}</span>
    {job.indexers.map((indexer) => {
      const state = indexer.status === 'queued' ? (finished ? 'skipped' : 'searching') : indexer.status
      const elapsed = indexer.elapsedMs == null ? '' : ` in ${(indexer.elapsedMs / 1000).toFixed(1)} s`
      const detail = state === 'searching' ? 'Still searching'
        : state === 'skipped' ? 'Did not answer before the search ended'
          : state === 'failed' ? `Failed${elapsed}: ${indexer.error || 'unknown error'}`
            : `${indexer.results} result${indexer.results === 1 ? '' : 's'}${elapsed}`
      return <Tooltip key={indexer.id} asChild delay={300} content={detail}>
        <span className={`search-indexer is-${state}`} aria-label={`${indexer.name}: ${detail}`}>
          {state === 'searching' ? <span className="search-indexer-spinner" aria-hidden="true" />
            : state === 'failed' ? <Icon name="alert" size={12} />
              : state === 'skipped' ? <Icon name="close" size={12} />
                : <Icon name="check" size={12} />}
          <span className="search-indexer-name">{indexer.name}</span>
          {state === 'completed' && <span className="search-indexer-count">{indexer.results}</span>}
        </span>
      </Tooltip>
    })}
  </div>
}
