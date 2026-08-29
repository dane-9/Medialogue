import type { Problem } from '../../types'
import type { components } from '../generated/schema'

type ProblemDto = components['schemas']['ProblemResponse']

export function mapProblem(value: ProblemDto): Problem {
  const subject = (value.subject ?? value.label) || value.entity_type
  return {
    id: value.id,
    code: value.reason,
    title: value.title || value.reason.replaceAll('_', ' '),
    subject,
    posterRef: value.poster_ref ?? undefined,
    tmdbId: value.tmdb_id ?? undefined,
    detail: value.description || value.message,
    severity: value.severity === 'error' ? 'high' : value.severity === 'info' ? 'low' : 'medium',
    created: new Date(value.created_at).toLocaleString(),
    reason: value.reason,
    status: value.status,
    workflow: value.workflow,
    label: value.label || undefined,
    entityType: value.entity_type,
    entityId: value.entity_id ?? undefined,
    details: value.details,
    resolution: value.resolution ?? undefined,
    resolvedAt: value.resolved_at ?? undefined,
    availableActions: value.available_actions,
  }
}
