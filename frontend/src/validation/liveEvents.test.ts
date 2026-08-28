import { describe, expect, it } from 'vitest'
import { parseJobStatusLiveEvent, parseLiveEventData } from './liveEvents'

const event = {
  event: 'job.status',
  entity_type: 'job',
  entity_id: 'job-1',
  timestamp: '2026-08-28T00:00:00+00:00',
  data: {
    job_id: 'job-1',
    job_type: 'storage_root_scan',
    status: 'running',
    progress: {},
    error: null,
    cancellable: true,
    revision: 2,
    created_at: '2026-08-28T00:00:00+00:00',
    started_at: '2026-08-28T00:00:01+00:00',
    finished_at: null,
    updated_at: '2026-08-28T00:00:01+00:00',
  },
}

describe('live event validation', () => {
  it('rejects malformed JSON and malformed envelopes', () => {
    expect(parseLiveEventData('{')).toBeNull()
    expect(parseLiveEventData(JSON.stringify({ event: 'job.status' }))).toBeNull()
  })

  it('validates job status payloads', () => {
    expect(parseJobStatusLiveEvent(JSON.stringify(event))?.data.job_type).toBe('storage_root_scan')
  })

  it('rejects impossible job states', () => {
    expect(parseJobStatusLiveEvent(JSON.stringify({ ...event, data: { ...event.data, status: 'banana' } }))).toBeNull()
  })
})
