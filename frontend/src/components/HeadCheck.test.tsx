// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HeadCheck } from './SelectionBar'

afterEach(cleanup)

const box = () => screen.getByRole('checkbox')

describe('HeadCheck', () => {
  it('selects every row when none or some are selected', () => {
    const onSelectAll = vi.fn(), onClear = vi.fn()
    const { rerender } = render(<HeadCheck selected={0} total={3} noun="titles" onSelectAll={onSelectAll} onClear={onClear} />)
    expect(box().getAttribute('aria-checked')).toBe('false')
    fireEvent.click(box())
    rerender(<HeadCheck selected={2} total={3} noun="titles" onSelectAll={onSelectAll} onClear={onClear} />)
    expect(box().getAttribute('aria-checked')).toBe('mixed')
    fireEvent.click(box())
    expect(onSelectAll).toHaveBeenCalledTimes(2)
    expect(onClear).not.toHaveBeenCalled()
  })

  it('clears every row when all are selected, and is off with nothing to select', () => {
    const onSelectAll = vi.fn(), onClear = vi.fn()
    const { rerender } = render(<HeadCheck selected={3} total={3} noun="titles" onSelectAll={onSelectAll} onClear={onClear} />)
    expect(box().getAttribute('aria-checked')).toBe('true')
    fireEvent.click(box())
    expect(onClear).toHaveBeenCalledOnce()
    rerender(<HeadCheck selected={0} total={0} noun="titles" onSelectAll={onSelectAll} onClear={onClear} />)
    expect((box() as HTMLButtonElement).disabled).toBe(true)
  })
})
