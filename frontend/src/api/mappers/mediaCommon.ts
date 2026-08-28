import type { Movie, MovieEvent, ReconciliationEvidence } from '../../types'
import type { components } from '../generated/schema'

type MovieSummaryDto = components['schemas']['MovieSummaryResponse']
type MovieProblemDto = components['schemas']['MovieProblemResponse']
type ShowProblemDto = components['schemas']['ShowProblemResponse']
type MovieEventDto = components['schemas']['MovieEventResponse']
type ShowEventDto = components['schemas']['ShowEventResponse']

export function mapPlexState(value: MovieSummaryDto['plex_state']): Movie['plex'] {
  switch (value) {
    case 'matched': return 'Verified'
    case 'multiple_versions': return 'Multiple versions'
    case 'conflict': return 'Conflict'
    case 'unavailable': return 'Unavailable'
    case 'not_found': return 'Not in Plex'
    case 'pending':
    case 'unknown':
      return 'Pending'
  }
}

export function mapEvidence(value: MovieProblemDto | ShowProblemDto): ReconciliationEvidence {
  return {
    id: value.id,
    code: value.reason,
    title: value.title || value.reason.replaceAll('_', ' '),
    detail: value.description || value.message,
    severity: value.severity === 'error' ? 'high' : value.severity === 'info' ? 'low' : 'medium',
  }
}

export function mapEvent(value: MovieEventDto | ShowEventDto): MovieEvent {
  return {
    id: value.id,
    type: value.type,
    message: value.message,
    details: value.details,
    createdAt: value.created_at,
  }
}
