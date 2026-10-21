import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

beforeEach(() => {
  window.history.replaceState(null, '', '/')
})

describe('application shell', () => {
  it.each([
    ['ask', 'Ask', 'Asking questions will be available in a later release.'],
    ['sources', 'Sources', 'Adding repositories will be available in a later release.'],
  ])('identifies the app and explains availability on %s', (route, heading, notice) => {
    window.history.replaceState(null, '', `/#/${route}`)
    render(<App />)
    expect(screen.getByText(/^Team Knowledge\s*Agent$/)).toBeVisible()
    const main = within(screen.getByRole('main'))
    expect(main.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    expect(main.getByText(notice)).toBeVisible()
  })

  it('navigates exactly Ask and Sources by pointer without a backend', async () => {
    const request = vi.spyOn(globalThis, 'fetch')
    const user = userEvent.setup()
    render(<App />)
    const nav = within(screen.getByRole('navigation', { name: 'Main navigation' }))
    expect(nav.getAllByRole('link').map(link => link.textContent)).toEqual(['Ask', 'Sources'])
    expect(nav.getByRole('link', { name: 'Ask' })).toHaveAttribute('aria-current', 'page')
    await user.click(nav.getByRole('link', { name: 'Sources' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
    expect(screen.getByText('No sources yet')).toBeVisible()
    expect(nav.getByRole('link', { name: 'Sources' })).toHaveAttribute('aria-current', 'page')
    await user.click(nav.getByRole('link', { name: 'Ask' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Ask' })).toBeVisible()
    expect(request).not.toHaveBeenCalled()
    request.mockRestore()
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
