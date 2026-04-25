import { describe, it, expect } from 'vitest'
import { API_BASE_URL } from './api'

describe('api', () => {
  it('exports a control-plane base URL', () => {
    expect(API_BASE_URL).toContain('8001')
  })
})
