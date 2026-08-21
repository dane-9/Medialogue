import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Job } from '../types'
import { Icon } from './Icon'
import { Button, Panel, Progress } from './ui'

// Background work used to live in a drawer while its durable record lived on
// this page, which meant two places to look for one thing. The drawer is gone:
// running work now sits at the top of the history it will become.
export function ActiveJobs() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    const refresh = () => api.jobs().then((payload) => { if (alive) setJobs(payload) }).catch(() => undefined)
    void refresh()

    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const listener = () => void refresh()
    stream.addEventListener('job.status', listener)
    // REST remains the recovery path when SSE reconnects or the browser slept.
    const timer = window.setInterval(refresh, 30000)
    return () => { alive = false; stream.close(); window.clearInterval(timer) }
  }, [])

  const cancel = async (job: Job) => {
    setError('')
    try {
      await api.cancelJob(job.id)
      setJobs(await api.jobs())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not cancel the job.')
    }
  }

  const active = jobs.filter((job) => job.state === 'running' || job.state === 'queued')
  if (!active.length && !error) return null

  return <Panel className="active-jobs" eyebrow="RUNNING NOW" title={active.length === 1 ? '1 background job' : `${active.length} background jobs`}>
    {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
    {active.map((job) => <div className="job-item" key={job.id}>
      <div className="job-item-top">
        <span className={`job-state job-${job.state}`}><span />{job.state}</span>
        <span className="muted">{job.updated}</span>
      </div>
      <strong>{job.title}</strong>
      <span className="job-detail">{job.error || job.detail || 'Persisted background operation'}</span>
      {job.progress !== undefined && <div className="job-progress-line"><Progress value={job.progress} tone="blue" /><span>{job.progress}%</span></div>}
      {job.cancellable && <div className="job-actions"><Button variant="ghost" onClick={() => void cancel(job)}>Cancel</Button></div>}
    </div>)}
  </Panel>
}
