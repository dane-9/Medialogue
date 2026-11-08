// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Link, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmLeave, useLeaveGuard } from './leaveGuard'

function Guarded({ active, ask }: { active: boolean; ask: () => Promise<boolean> }) {
  useLeaveGuard(active, ask)
  return <Link to="/elsewhere">Leave</Link>
}

function renderAt(active: boolean, ask: () => Promise<boolean>) {
  return render(<MemoryRouter initialEntries={['/settings']}><Routes>
    <Route path="/settings" element={<Guarded active={active} ask={ask} />} />
    <Route path="/elsewhere" element={<p>Elsewhere</p>} />
  </Routes></MemoryRouter>)
}

afterEach(() => cleanup())

describe('useLeaveGuard', () => {
  it('stays when the guard refuses, and leaves when it agrees', async () => {
    const ask = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    renderAt(true, ask)
    fireEvent.click(screen.getByText('Leave'))
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('Elsewhere')).toBeNull()
    fireEvent.click(screen.getByText('Leave'))
    expect(await screen.findByText('Elsewhere')).toBeTruthy()
  })

  it('does not ask when nothing is unsaved', async () => {
    const ask = vi.fn()
    renderAt(false, ask)
    expect(await confirmLeave()).toBe(true)
    fireEvent.click(screen.getByText('Leave'))
    expect(await screen.findByText('Elsewhere')).toBeTruthy()
    expect(ask).not.toHaveBeenCalled()
  })

  it('answers confirmLeave for tab switches', async () => {
    renderAt(true, async () => false)
    expect(await confirmLeave()).toBe(false)
  })
})
