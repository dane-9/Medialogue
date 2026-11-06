export function numberValue(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? value
    : Number.isFinite(Number(value))
      ? Number(value)
      : fallback
}

export function percentValue(value: number | null | undefined): number | undefined {
  if (value === null || value === undefined || !Number.isFinite(value)) return undefined
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value))
}
