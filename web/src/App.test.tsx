import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })))
})

afterEach(() => vi.unstubAllGlobals())

describe('application shell', () => {
  it.each([
    ['ask', 'Ask', 'Enter to send · Shift+Enter for a new line'],
    ['sources', 'Sources', 'Add a repository to make its code and docs available to your team.'],
  ])('identifies the app and explains availability on %s', async (route, heading, notice) => {
    window.history.replaceState(null, '', `/#/${route}`)
    render(<App />)
    expect(screen.getByText(/^Team Knowledge\s*Agent$/)).toBeVisible()
    const main = within(screen.getByRole('main'))
    expect(main.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    expect(await main.findByText(notice)).toBeVisible()
  })

  it('navigates exactly Ask and Sources by pointer, loading sources from the API', async () => {
    const user = userEvent.setup()
    render(<App />)
    const nav = within(screen.getByRole('navigation', { name: 'Main navigation' }))
    expect(nav.getAllByRole('link').map(link => link.textContent)).toEqual(['Ask', 'Sources'])
    expect(nav.getByRole('link', { name: 'Ask' })).toHaveAttribute('aria-current', 'page')
    await user.click(nav.getByRole('link', { name: 'Sources' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
    expect(await screen.findByText('No sources yet')).toBeVisible()
    expect(nav.getByRole('link', { name: 'Sources' })).toHaveAttribute('aria-current', 'page')
    await user.click(nav.getByRole('link', { name: 'Ask' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Ask' })).toBeVisible()
    expect(fetch).toHaveBeenCalledWith('/api/sources')
  })

  it('navigates by Tab and Enter and exposes a skip link', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.tab()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('link', { name: 'Ask' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('link', { name: 'Sources' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
    await user.tab({ shift: true })
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { level: 1, name: 'Ask' })).toBeVisible()
  })

  it('supports a direct Sources link and browser history', async () => {
    window.history.replaceState(null, '', '/#/sources')
    const user = userEvent.setup()
    render(<App />)
    expect(screen.getByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
    await user.click(screen.getByRole('link', { name: 'Ask' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Ask' })).toBeVisible()
    window.history.back()
    expect(await screen.findByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
  })

  it('skips to content without leaving Sources', async () => {
    window.history.replaceState(null, '', '/#/sources')
    const user = userEvent.setup()
    render(<App />)
    await user.tab()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('main')).toHaveFocus()
    expect(screen.getByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
    expect(window.location.hash).toBe('#/sources')
  })
})
