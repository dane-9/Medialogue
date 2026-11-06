import { describe, expect, it } from 'vitest'
import { jobEventChangesBadges } from './badgeRefresh'

const event = (job_type: string, status: string) => JSON.stringify({
  event: 'job.status',
  entity_type: 'job',
  entity_id: 'job-1',
  timestamp: '2026-11-06T12:00:00Z',
  data: {
    job_id: 'job-1', job_type, status, progress: {}, error: null, cancellable: false, revision: 1,
    created_at: null, started_at: null, finished_at: null, updated_at: null,
  },
})

describe('jobEventChangesBadges', () => {
  it('refreshes when a relevant job finishes', () => {
    expect(jobEventChangesBadges(event('search_media_upgrades', 'completed'))).toBe(true)
    expect(jobEventChangesBadges(event('storage_root_scan', 'failed'))).toBe(true)
  })

  it('ignores jobs that are still queued or running', () => {
    expect(jobEventChangesBadges(event('storage_root_scan', 'running'))).toBe(false)
    expect(jobEventChangesBadges(event('search_media_upgrades', 'queued'))).toBe(false)
  })

  it('ignores the health sweep and backups', () => {
    expect(jobEventChangesBadges(event('integration_health_sweep', 'completed'))).toBe(false)
    expect(jobEventChangesBadges(event('remote_backup', 'completed'))).toBe(false)
  })

  it('refreshes when the event cannot be read', () => {
    expect(jobEventChangesBadges('not json')).toBe(true)
  })
})
