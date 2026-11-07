import { useEffect, useState } from 'react'
import { formatAge } from '../lib/age'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { Badge, Button, EmptyState, Panel, Select } from '../components/ui'
import { systemApi } from '../api/modules/system'
import { isTaskOverdue } from '../lib/taskTiming'
import type { Job, ScheduledTask } from '../types'
import { Page } from './shared'
import { openLiveEvents } from '../lib/liveEvents'

function taskInterval(seconds: number) {
  if (seconds < 60) return `${seconds} seconds`
  if (seconds % 604800 === 0) { const weeks = seconds / 604800; return `${weeks} week${weeks === 1 ? '' : 's'}` }
  if (seconds % 86400 === 0) { const days = seconds / 86400; return `${days} day${days === 1 ? '' : 's'}` }
  if (seconds % 3600 === 0) { const hours = seconds / 3600; return `${hours} hour${hours === 1 ? '' : 's'}` }
  const minutes = Math.round(seconds / 60)
  return `${minutes} minute${minutes === 1 ? '' : 's'}`
}

function taskTime(value?: string, future = false, now = Date.now()) {
  if (!value) return '-'
  if (!future) return formatAge(value)
  const remaining = Date.parse(value) - now
  if (!Number.isFinite(remaining) || remaining <= 0) return 'now'
  const seconds = Math.ceil(remaining / 1000)
  if (seconds < 60) return `in ${seconds} seconds`
  if (seconds >= 86400) { const days = Math.ceil(seconds / 86400); return `in ${days} day${days === 1 ? '' : 's'}` }
  if (seconds >= 3600) { const hours = Math.ceil(seconds / 3600); return `in ${hours} hour${hours === 1 ? '' : 's'}` }
  const minutes = Math.ceil(seconds / 60)
  return `in ${minutes} minute${minutes === 1 ? '' : 's'}`
}

function taskDuration(milliseconds?: number) {
  if (milliseconds === undefined) return '-'
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function jobDuration(job: Job, now = Date.now()) {
  if (!job.startedAt) return '-'
  const started = Date.parse(job.startedAt)
  const finished = job.finishedAt ? Date.parse(job.finishedAt) : now
  return Number.isFinite(started) && Number.isFinite(finished) ? taskDuration(finished - started) : '-'
}

export function TasksPage() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [runningId, setRunningId] = useState('')
  const [updatingId, setUpdatingId] = useState('')
  const [cancellingId, setCancellingId] = useState('')
  const [error, setError] = useState('')
  const [clock, setClock] = useState(() => Date.now())

  const load = async (foreground = false) => {
    if (foreground) setLoading(true)
    try {
      const [scheduled, recent] = await Promise.all([systemApi.scheduledTasks(), systemApi.jobs()])
      setTasks(scheduled)
      setJobs(recent.slice(0, 30))
      setError('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load scheduled tasks.') }
    finally { if (foreground) setLoading(false) }
  }

  useEffect(() => {
    void load(true)
    const stream = openLiveEvents()
    const changed = () => void load()
    stream.addEventListener('job.status', changed)
    const timer = window.setInterval(changed, 10_000)
    return () => { stream.close(); window.clearInterval(timer) }
  }, [])

  // The server data only needs its normal ten-second refresh. A local clock
  // keeps countdowns and running durations accurate without extra API calls.
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [])

  const run = async (task: ScheduledTask) => {
    setRunningId(task.id); setError('')
    try { await systemApi.runScheduledTask(task.id); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : `Could not run ${task.name}.`) }
    finally { setRunningId('') }
  }

  const updateInterval = async (task: ScheduledTask, value: string) => {
    setUpdatingId(task.id); setError('')
    try {
      const updated = await systemApi.updateScheduledTask(task.id, value === 'manual' ? null : Number(value))
      setTasks((current) => current.map((item) => item.id === updated.id ? updated : item))
    } catch (reason) { setError(reason instanceof Error ? reason.message : `Could not update ${task.name}.`) }
    finally { setUpdatingId('') }
  }

  const cancel = async (job: Job) => {
    setCancellingId(job.id); setError('')
    try { await systemApi.cancelJob(job.id); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : `Could not cancel ${job.title}.`) }
    finally { setCancellingId('') }
  }

  return <Page title="Tasks" subtitle="Available tasks, active background work, and recent executions in one place.">
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    <Panel className="table-panel tasks-panel" title="Scheduled" note={`${tasks.length} TASK${tasks.length === 1 ? '' : 'S'}`}>
      <div className="tasks-table-wrap"><table className="data-table tasks-table"><thead><tr><th>Name</th><th>Interval</th><th>Last execution</th><th>Last duration</th><th>Next execution</th><th /></tr></thead><tbody>{tasks.map((task) => {
          const active = Boolean(task.activeJobId)
          const overdue = task.enabled && !active && isTaskOverdue(task.nextRunAt, task.intervalSeconds, clock)
          return <tr key={task.id}><td><strong>{task.name}</strong><span className="table-sub">{task.description}</span></td><td>{task.configurable ? <Select aria-label={`Interval for ${task.name}`} value={task.enabled ? String(task.intervalSeconds) : 'manual'} disabled={updatingId === task.id} onChange={(event) => void updateInterval(task, event.target.value)}><option value="manual">Manual only</option>{task.intervalOptions.map((seconds) => <option value={seconds} key={seconds}>{taskInterval(seconds)}</option>)}</Select> : taskInterval(task.intervalSeconds)}</td><td>{taskTime(task.lastFinishedAt)}</td><td className="task-duration">{taskDuration(task.lastDurationMs)}</td><td>{active ? <Badge tone="blue">{task.activeJobStatus ?? 'queued'}</Badge> : overdue ? <Tooltip asChild content="The scheduler has not started this task within its expected grace period"><span><Badge tone="red">Overdue</Badge></span></Tooltip> : task.enabled ? taskTime(task.nextRunAt, true, clock) : 'Manual only'}</td><td><Tooltip asChild content={active ? 'Task is already running' : `Run ${task.name} now`}><button className="icon-button" aria-label={`Run ${task.name} now`} disabled={active || runningId === task.id} onClick={() => void run(task)}><Icon name={active ? 'activity' : 'refresh'} size={15} /></button></Tooltip></td></tr>
        })}</tbody></table></div>
      {!loading && !tasks.length && <EmptyState icon="activity" title="No available tasks" detail="No background tasks are registered." />}
    </Panel>
    <Panel className="table-panel tasks-panel" title="History" note={`${jobs.length} MOST RECENT`}>
      <div className="tasks-table-wrap"><table className="data-table tasks-table tasks-queue-table"><thead><tr><th>Name</th><th>Started</th><th>Ended</th><th>Duration</th><th>Progress</th><th>Details</th><th /></tr></thead><tbody>{jobs.map((job) => {
          const canCancel = job.cancellable && (job.state === 'running' || job.state === 'queued')
          const terminal = ['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state)
          const progress = terminal
            ? <Badge tone={job.state === 'completed' ? 'green' : job.state === 'failed' || job.state === 'interrupted' ? 'red' : 'neutral'}>{job.state}</Badge>
            : <Badge tone="blue">{job.state}{job.progress !== undefined ? ` · ${job.progress}%` : ''}</Badge>
          return <tr key={job.id}><td><strong>{job.title}</strong></td><td>{taskTime(job.startedAt)}</td><td>{taskTime(job.finishedAt)}</td><td className="task-duration">{jobDuration(job, clock)}</td><td>{progress}</td><td className="path-cell">{job.error || job.detail || '-'}</td><td>{canCancel && <Button variant="ghost" onClick={() => void cancel(job)} disabled={cancellingId === job.id}>{cancellingId === job.id ? 'Cancelling…' : 'Cancel'}</Button>}</td></tr>
        })}</tbody></table></div>
      {!loading && !jobs.length && <EmptyState icon="activity" title="No background work yet" detail="Scheduled and manually started executions will appear here." />}
    </Panel>
  </Page>
}
