import { Fragment, useLayoutEffect, useState } from 'react'
import type { ReactNode } from 'react'

// A library can hold thousands of titles. Rendering only the rows near the
// viewport keeps scrolling smooth and the first paint fast; spacers stand in
// for everything above and below, so the page keeps its full height and the
// page's scrollbar behaves as if every row were there.

type Range = { start: number; end: number }

const OVERSCAN_PX = 900

function sameRange(a: Range, b: Range) { return a.start === b.start && a.end === b.end }

/** Watches page scrolling and reports which rows of a list fall in view. */
function useWindowRange(container: HTMLElement | null, rowCount: number, rowHeight: number) {
  const [range, setRange] = useState<Range>({ start: 0, end: Math.min(rowCount, 12) })
  useLayoutEffect(() => {
    if (!container) return
    let frame = 0
    const update = () => {
      frame = 0
      const top = container.getBoundingClientRect().top
      const height = Math.max(rowHeight, 1)
      const start = Math.max(0, Math.floor((-top - OVERSCAN_PX) / height))
      const end = Math.min(rowCount, Math.ceil((window.innerHeight - top + OVERSCAN_PX) / height))
      const next = { start: Math.min(start, end), end }
      // Only a change of range re-renders, not every scroll event.
      setRange((current) => sameRange(current, next) ? current : next)
    }
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(update) }
    update()
    // Capturing, because the page scrolls in an element and that does not bubble.
    window.addEventListener('scroll', schedule, { passive: true, capture: true })
    window.addEventListener('resize', schedule)
    return () => {
      window.removeEventListener('scroll', schedule, { capture: true })
      window.removeEventListener('resize', schedule)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [container, rowCount, rowHeight])
  return range
}

/**
 * A CSS grid that renders only its visible rows. The grid's own stylesheet
 * still decides the columns and gaps; this reads them back so the layout is
 * unchanged at every width. Every item must be the same height.
 */
export function VirtualGrid<T>({ items, className, itemKey, renderItem }: { items: T[]; className: string; itemKey: (item: T) => string; renderItem: (item: T) => ReactNode }) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null)
  const [metrics, setMetrics] = useState({ columns: 1, rowHeight: 300, rowGap: 0 })
  const hasItems = items.length > 0

  useLayoutEffect(() => {
    if (!container) return
    const measure = () => {
      const style = window.getComputedStyle(container)
      const columns = Math.max(1, style.gridTemplateColumns.split(' ').filter(Boolean).length)
      const rowGap = Number.parseFloat(style.rowGap) || 0
      const card = container.querySelector<HTMLElement>(':scope > :not(.virtual-spacer)')
      const rowHeight = card ? card.getBoundingClientRect().height + rowGap : 300
      setMetrics((current) => current.columns === columns && current.rowGap === rowGap && Math.abs(current.rowHeight - rowHeight) < 0.5 ? current : { columns, rowHeight, rowGap })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(container)
    return () => observer.disconnect()
  }, [container, hasItems])

  const rowCount = Math.ceil(items.length / metrics.columns)
  const range = useWindowRange(container, rowCount, metrics.rowHeight)
  const visible = items.slice(range.start * metrics.columns, range.end * metrics.columns)
  // A spacer spans every column and stands in for whole rows. The grid adds
  // one row gap after it, so the spacer leaves that gap out of its height.
  const spacer = (rows: number, key: string) => rows > 0
    ? <div key={key} className="virtual-spacer" aria-hidden="true" style={{ gridColumn: '1 / -1', height: rows * metrics.rowHeight - metrics.rowGap }} />
    : null
  return <div ref={setContainer} className={className}>
    {spacer(range.start, 'before')}
    {visible.map((item) => <Fragment key={itemKey(item)}>{renderItem(item)}</Fragment>)}
    {spacer(rowCount - range.end, 'after')}
  </div>
}

/**
 * Table rows rendered near the viewport only. Rows are assumed to share one
 * height, measured from the rows on screen; a spacer row of that height times
 * the hidden count holds each side open.
 */
export function useVirtualRows<T>(items: T[], columnCount: number) {
  const [body, setBody] = useState<HTMLTableSectionElement | null>(null)
  const [rowHeight, setRowHeight] = useState(48)
  const range = useWindowRange(body, items.length, rowHeight)

  useLayoutEffect(() => {
    if (!body) return
    const rows = [...body.querySelectorAll<HTMLTableRowElement>(':scope > tr:not(.virtual-spacer)')]
    if (!rows.length) return
    const average = rows.reduce((sum, row) => sum + row.getBoundingClientRect().height, 0) / rows.length
    if (Math.abs(average - rowHeight) >= 0.5) setRowHeight(average)
  })

  const spacer = (count: number, key: string) => count > 0
    ? <tr key={key} className="virtual-spacer" aria-hidden="true"><td colSpan={columnCount} style={{ height: count * rowHeight, padding: 0, border: 0 }} /></tr>
    : null
  return {
    bodyRef: setBody,
    visible: items.slice(range.start, range.end),
    before: spacer(range.start, 'before'),
    after: spacer(items.length - range.end, 'after'),
  }
}
