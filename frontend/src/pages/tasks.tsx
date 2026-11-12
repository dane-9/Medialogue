import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { formatAge } from '../lib/age'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { Badge, Button, EmptyState, Input, Panel, Select } from '../components/ui'
import { systemApi } from '../api/modules/system'
import { isTaskOverdue } from '../lib/taskTiming'
import type { Job, ScheduledTask } from '../types'
import { Page } from './shared'
import { useLiveRefresh } from '../lib/liveRefresh'
import { useUrlNumber, useUrlState } from '../lib/urlState'

const HISTORY_PAGE_SIZES = [50, 100, 250, 500, 1000]

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
  // Running scheduled tasks whose job is not on the History page shown, so they can still be stopped.
  const [activeJobs, setActiveJobs] = useState<Job[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyPages, setHistoryPages] = useState(0)
  const [historyPage, setHistoryPage] = useUrlNumber('page', 1)
  const [search, setSearch] = useUrlState('q')
  const [query, setQuery] = useState(search)
  const [pageSize, setPageSize] = useState(50)
  const [loading, setLoading] = useState(true)
  const [runningId, setRunningId] = useState('')
  const [updatingId, setUpdatingId] = useState('')
  const [cancellingId, setCancellingId] = useState('')
  const [error, setError] = useState('')
  const [clock, setClock] = useState(() => Date.now())

  // Typing settles for a moment before it searches.
  useEffect(() => {
    if (search === query) return
    const timer = window.setTimeout(() => { setQuery(search); setHistoryPage(1) }, 300)
    return () => window.clearTimeout(timer)
  }, [search, query, setHistoryPage])

  // Only the newest request may update the page, so a slow one cannot overwrite a newer search.
  const requestRef = useRef(0)
  const load = async (foreground = false) => {
    const request = ++requestRef.current
    if (foreground) setLoading(true)
    try {
      const [scheduled, history] = await Promise.all([systemApi.scheduledTasks(), systemApi.jobHistory({ search: query, page: historyPage, pageSize })])
      const shown = new Set(history.items.map((job) => job.id))
      const missing = scheduled.flatMap((task) => task.activeJobId && !shown.has(task.activeJobId) ? [task.activeJobId] : [])
      const active = (await Promise.all(missing.map((id) => systemApi.job(id).catch(() => undefined)))).filter((job): job is Job => Boolean(job))
      if (request !== requestRef.current) return
      setTasks(scheduled)
      setJobs(history.items)
      setActiveJobs(active)
      setHistoryTotal(history.total)
      setHistoryPages(history.pages)
      if (history.pages && historyPage > history.pages) setHistoryPage(history.pages)
      setError('')
    } catch (reason) { if (request === requestRef.current) setError(reason instanceof Error ? reason.message : 'Could not load scheduled tasks.') }
    finally { if (foreground && request === requestRef.current) setLoading(false) }
  }

  useEffect(() => { void load(true) }, [query, historyPage, pageSize])
  useLiveRefresh(() => load(), { events: ['job.status'], pollMs: 10_000 })

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
          const stoppable = active ? [...jobs, ...activeJobs].find((job) => job.id === task.activeJobId && job.cancellable) : undefined
          return <tr key={task.id}><td><strong>{task.name}</strong><span className="table-sub">{task.description}</span></td><td>{task.configurable ? <Select aria-label={`Interval for ${task.name}`} value={task.enabled ? String(task.intervalSeconds) : 'manual'} disabled={updatingId === task.id} onChange={(event) => void updateInterval(task, event.target.value)}><option value="manual">Manual only</option>{task.intervalOptions.map((seconds) => <option value={seconds} key={seconds}>{taskInterval(seconds)}</option>)}</Select> : task.jobType === 'remote_backup' ? <Link to="/backups?tab=drive">{task.enabled ? `${taskInterval(task.intervalSeconds)} · Backups page` : 'Off · Backups page'}</Link> : taskInterval(task.intervalSeconds)}</td><td>{taskTime(task.lastFinishedAt)}</td><td className="task-duration">{taskDuration(task.lastDurationMs)}</td><td>{active ? <Badge tone="blue">{task.activeJobStatus ?? 'queued'}</Badge> : overdue ? <Tooltip asChild content="The scheduler has not started this task within its expected grace period"><span><Badge tone="red">Overdue</Badge></span></Tooltip> : task.enabled ? taskTime(task.nextRunAt, true, clock) : 'Manual only'}</td><td>{stoppable
            ? <Tooltip asChild content={`Cancel ${task.name}`}><button className="icon-button" aria-label={`Cancel ${task.name}`} disabled={cancellingId === stoppable.id} onClick={() => void cancel(stoppable)}><Icon name="stop" size={15} /></button></Tooltip>
            : <Tooltip asChild content={active ? 'Task is already running' : `Run ${task.name} now`}><button className="icon-button" aria-label={`Run ${task.name} now`} disabled={active || runningId === task.id} onClick={() => void run(task)}><Icon name={active ? 'activity' : 'refresh'} size={15} /></button></Tooltip>}</td></tr>
        })}</tbody></table></div>
      {!loading && !tasks.length && <EmptyState icon="activity" title="No available tasks" detail="No background tasks are registered." />}
    </Panel>
    <Panel className="table-panel tasks-panel" title="History" note={`${historyTotal.toLocaleString()} ${query ? 'MATCHING' : 'IN TOTAL'}`} action={<div className="tasks-history-controls">
      <div className="search-field compact"><Icon name="search" size={16} /><Input aria-label="Search history" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search history…" /></div>
      <Select aria-label="History entries per page" value={String(pageSize)} onChange={(event) => { setPageSize(Number(event.target.value)); setHistoryPage(1) }}>{HISTORY_PAGE_SIZES.map((size) => <option key={size} value={size}>{size} per page</option>)}</Select>
    </div>}>
      <div className="tasks-table-wrap"><table className="data-table tasks-table tasks-queue-table"><thead><tr><th>Name</th><th>Started</th><th>Ended</th><th>Duration</th><th>Progress</th><th>Details</th><th /></tr></thead><tbody>{jobs.map((job) => {
          const canCancel = job.cancellable && (job.state === 'running' || job.state === 'queued')
          const terminal = ['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state)
          const progress = terminal
            ? <Badge tone={job.state === 'completed' ? 'green' : job.state === 'failed' || job.state === 'interrupted' ? 'red' : 'neutral'}>{job.state}</Badge>
            : <Badge tone="blue">{job.state}{job.progress !== undefined ? ` · ${job.progress}%` : ''}</Badge>
          return <tr key={job.id}><td><strong>{job.title}</strong></td><td>{taskTime(job.startedAt)}</td><td>{taskTime(job.finishedAt)}</td><td className="task-duration">{jobDuration(job, clock)}</td><td>{progress}</td><td className="path-cell">{job.error || job.detail || '-'}</td><td>{canCancel && <Button variant="ghost" onClick={() => void cancel(job)} disabled={cancellingId === job.id}>{cancellingId === job.id ? 'Cancelling…' : 'Cancel'}</Button>}</td></tr>
        })}</tbody></table></div>
      {!loading && !jobs.length && (query
        ? <EmptyState icon="search" title="No matching history" detail={`Nothing in History matches “${query}”.`} />
        : <EmptyState icon="activity" title="No background work yet" detail="Scheduled and manually started executions will appear here." />)}
    </Panel>
    {historyPages > 1 && <div className="pagination-bar"><Button variant="ghost" disabled={historyPage <= 1} onClick={() => setHistoryPage((value) => Math.max(1, value - 1))}>Previous</Button><span>Page {historyPage} of {historyPages} · {historyTotal.toLocaleString()} executions</span><Button variant="ghost" disabled={historyPage >= historyPages} onClick={() => setHistoryPage((value) => Math.min(historyPages, value + 1))}>Next</Button></div>}
  </Page>
}
