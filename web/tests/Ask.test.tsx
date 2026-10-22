import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Ask } from '../src/components/Ask'
import type { AnswerResponse } from '../src/answer'
import { deployCitation } from './citations-fixtures'

const known: AnswerResponse = {
  known: true,
  answer: 'Deploys are frozen on Fridays after 14:00 UTC. [1]',
  citations: [deployCitation],
}
const unknown: AnswerResponse = {
  known: false,
  answer: "I don't know. I couldn't find this in the indexed sources.",
  citations: [],
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function stubTheme(theme: 'dark' | 'light') {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('light') ? theme === 'light' : theme === 'dark',
    media: query, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }))
}

afterEach(() => { vi.unstubAllGlobals() })

HTMLElement.prototype.scrollIntoView = vi.fn()

describe.each(['dark', 'light'] as const)('Ask in the %s theme', theme => {
  it('does nothing for an empty question', async () => {
    stubTheme(theme)
    const ask = vi.fn()
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), '   {Enter}')
    expect(ask).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: 'Answer' })).not.toBeInTheDocument()
  })

  it('submits on Enter once and inserts a newline with no request on Shift+Enter', async () => {
    stubTheme(theme)
    const ask = vi.fn().mockResolvedValue(known)
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    const box = screen.getByRole('textbox', { name: 'Your question' })
    await user.type(box, 'line one{Shift>}{Enter}{/Shift}line two')
    expect(ask).not.toHaveBeenCalled()
    expect(box).toHaveValue('line one\nline two')
    await user.keyboard('{Enter}')
    expect(ask).toHaveBeenCalledOnce()
    expect(ask).toHaveBeenCalledWith('line one\nline two')
    expect(await screen.findByRole('button', { name: 'Show citation 1' })).toBeVisible()
  })

  it('shows a skeleton while pending and ignores duplicate sends', async () => {
    stubTheme(theme)
    let resolve!: (value: AnswerResponse) => void
    const ask = vi.fn(() => new Promise<AnswerResponse>(done => { resolve = done }))
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'When are deploys frozen?{Enter}')
    expect(screen.getByRole('status')).toHaveTextContent('Searching the indexed sources')
    expect(screen.getByRole('button', { name: 'Asking…' })).toBeDisabled()
    await user.keyboard('{Enter}')
    await user.click(screen.getByRole('button', { name: 'Asking…' }))
    expect(ask).toHaveBeenCalledOnce()
    resolve(known)
    expect(await screen.findByRole('button', { name: 'Show citation 1' })).toBeVisible()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('renders the known deploy answer with inline chips and the exact API citation URL', async () => {
    stubTheme(theme)
    const fetchMock = vi.fn().mockResolvedValue(json(known))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<Ask />)
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'When are deploys frozen?{Enter}')
    const card = await screen.findByRole('article', { name: 'docs/deploy.md' })
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/ask')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ question: 'When are deploys frozen?' })
    expect(screen.getByRole('button', { name: 'Show citation 1' })).toHaveTextContent('[1]')
    expect(within(card).getByRole('link', { name: /Open on GitHub/ })).toHaveAttribute('href', deployCitation.url)
    await user.click(screen.getByRole('button', { name: 'Show citation 1' }))
    expect(card).toHaveFocus()
  })

  it('shows the knowledge gap with the hint and zero citation cards', async () => {
    stubTheme(theme)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(unknown)))
    const user = userEvent.setup()
    render(<Ask />)
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'What is the parental leave policy?{Enter}')
    const gap = await screen.findByText(/couldn.t find this in the indexed sources/)
    expect(gap).toBeVisible()
    expect(screen.getByText(/adding or re-indexing a source/)).toBeVisible()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('shows the failure message and Retry resubmits the same question once', async () => {
    stubTheme(theme)
    const ask = vi.fn().mockRejectedValueOnce(new Error('Answers are unavailable')).mockResolvedValue(known)
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'When are deploys frozen?{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('Answers are unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(ask).toHaveBeenCalledTimes(2)
    expect(ask).toHaveBeenLastCalledWith('When are deploys frozen?')
    expect(await screen.findByRole('button', { name: 'Show citation 1' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('surfaces the server detail for a 503 and a generic message for a network failure', async () => {
    stubTheme(theme)
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ detail: 'Answers are unavailable' }, 503))
      .mockRejectedValueOnce(new TypeError('failed'))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<Ask />)
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'q{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('Answers are unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Could not get an answer. Please try again.')).toBeVisible()
  })

  it('never renders source HTML as markup', async () => {
    stubTheme(theme)
    const hostile: AnswerResponse = { ...known, answer: '<img src=x onerror="alert(1)"> [1]' }
    const user = userEvent.setup()
    const { container } = render(<Ask ask={() => Promise.resolve(hostile)} />)
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'q{Enter}')
    await screen.findByText(/<img src=x/)
    expect(container.querySelector('img')).toBeNull()
  })
})
