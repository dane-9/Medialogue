export function isTaskOverdue(nextRunAt: string | undefined, intervalSeconds: number, now = Date.now()) {
  if (!nextRunAt) return false
  const dueAt = Date.parse(nextRunAt)
  if (!Number.isFinite(dueAt)) return false
  // The scheduler checks every five seconds. Allow ordinary execution and
  // database jitter without masking a genuinely stalled scheduler.
  const graceMs = Math.max(15_000, Math.min(60_000, intervalSeconds * 250))
  return now - dueAt > graceMs
}
