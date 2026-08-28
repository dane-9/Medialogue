import { z } from 'zod'

const liveEventSchema = z.object({
  event: z.string().min(1),
  entity_type: z.string().min(1),
  entity_id: z.string().nullable(),
  timestamp: z.string().min(1),
  data: z.record(z.string(), z.unknown()),
})

const jobStatusDataSchema = z.object({
  job_id: z.string().min(1),
  job_type: z.string().min(1),
  status: z.enum(['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted']),
  progress: z.record(z.string(), z.unknown()),
  error: z.record(z.string(), z.unknown()).nullable(),
  cancellable: z.boolean(),
  revision: z.number().int(),
  created_at: z.string().nullable(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
  updated_at: z.string().nullable(),
})

export type LiveEvent = {
  event: string
  entity_type: string
  entity_id: string | null
  timestamp: string
  data: Record<string, unknown>
}

type JobStatusData = {
  job_id: string
  job_type: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  progress: Record<string, unknown>
  error: Record<string, unknown> | null
  cancellable: boolean
  revision: number
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  updated_at: string | null
}

export type JobStatusLiveEvent = LiveEvent & { data: JobStatusData }

export function parseLiveEventData(raw: string): LiveEvent | null {
  try {
    const parsed: unknown = JSON.parse(raw)
    const result = liveEventSchema.safeParse(parsed)
    return result.success ? result.data as LiveEvent : null
  } catch {
    return null
  }
}

export function parseJobStatusLiveEvent(raw: string): JobStatusLiveEvent | null {
  const event = parseLiveEventData(raw)
  if (!event || event.event !== 'job.status') return null
  const data = jobStatusDataSchema.safeParse(event.data)
  return data.success ? { ...event, data: data.data as JobStatusData } : null
}
