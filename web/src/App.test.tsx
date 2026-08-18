import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

describe('Windcode Web shell', () => {
  afterEach(() => vi.unstubAllGlobals())

  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ items: [] }), {
        headers: { 'content-type': 'application/json' },
      })),
    )
  })

  it('shows the Chinese workspace blocker before a project is selected', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: '选择工作区' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '添加' }).hasAttribute('disabled')).toBe(true)
  })
})
