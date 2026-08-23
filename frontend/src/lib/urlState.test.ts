import { describe, expect, it } from 'vitest'

import { updateUrlParams } from './urlState'

describe('URL state batches', () => {
  it('changes a Problems workflow while clearing selection and pagination atomically', () => {
    const current = new URLSearchParams('problem=problem-1&page=3&severity=high')

    const updated = updateUrlParams(current, {
      workflow: 'choice',
      problem: null,
      page: null,
    })

    expect(updated.get('workflow')).toBe('choice')
    expect(updated.has('problem')).toBe(false)
    expect(updated.has('page')).toBe(false)
    expect(updated.get('severity')).toBe('high')
  })

  it('removes fallback filters without disturbing unrelated query state', () => {
    const current = new URLSearchParams('workflow=manual&reason=identity')

    const updated = updateUrlParams(current, { workflow: null })

    expect(updated.has('workflow')).toBe(false)
    expect(updated.get('reason')).toBe('identity')
  })
})
