import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Ask } from '../src/components/Ask'
import type { AnswerResponse } from '../src/answer'
import { deployCitation, longCitation } from './citations-fixtures'

const known: AnswerResponse = {
  known: true,
  answer: 'Deploys are frozen on Fridays after 14:00 UTC. [1]',
  citations: [deployCitation],
}
const second: AnswerResponse = {
  known: true,
  answer: 'Run the release checklist first. [1] Then deploy one region at a time. [2]',
  citations: [{ ...deployCitation, n: 1, path: 'docs/checklist.md' }, longCitation],
}
const unknown: AnswerResponse = {
  known: false,
  answer: "I don't know. I couldn't find this in the indexed sources.",
  citations: [],
}
const library = { repositories: 2, files: 193, chunks: 610, busy: 0 }
const suggestions = [
  { label: 'Get started', question: 'How do I get started with alpha?' },
  { label: 'Structure', question: 'How is beta organised?' },
]

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** The citation panel opens by itself only where it is a column (>= 1200px). */
function stubViewport(wide: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: wide && query.includes('min-width'),
    media: query, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }))
}

afterEach(() => { vi.unstubAllGlobals() })

HTMLElement.prototype.scrollIntoView = vi.fn()

const box = () => screen.getByRole('textbox', { name: 'Your question' })
const thread = () => within(screen.getByRole('log', { name: 'Messages' }))

describe('Ask as a chat thread', () => {
  it('starts with a hero, the library context and suggestion cards, and nothing to clear', () => {
    render(<Ask library={library} suggestions={suggestions} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Ask' })).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: 'Ask your team’s knowledge' })).toBeVisible()
    expect(screen.getByText(/Answering from 2 repositories/)).toHaveTextContent('193 files')
    expect(screen.getAllByRole('button', { name: /How (do I get started|is beta)/ })).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'New chat' })).toBeDisabled()
    expect(screen.queryByRole('log')).not.toBeInTheDocument()
  })

  it('points to Sources when there is nothing to ask about', () => {
    render(<Ask library={{ repositories: 0, files: 0, chunks: 0, busy: 0 }} />)
    expect(screen.getByRole('link', { name: 'Add a repository' })).toHaveAttribute('href', '#/sources')
  })

  it('does nothing for an empty question', async () => {
    const ask = vi.fn()
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await user.type(box(), '   {Enter}')
    expect(ask).not.toHaveBeenCalled()
    expect(screen.queryByRole('log')).not.toBeInTheDocument()
  })

  it('sends on Enter once, clears the composer and inserts a newline on Shift+Enter without a request', async () => {
    const ask = vi.fn().mockResolvedValue(known)
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(box(), 'line one{Shift>}{Enter}{/Shift}line two')
    expect(ask).not.toHaveBeenCalled()
    expect(box()).toHaveValue('line one\nline two')
    await user.keyboard('{Enter}')
    expect(ask).toHaveBeenCalledExactlyOnceWith('line one\nline two')
    expect(box()).toHaveValue('')
    expect(await screen.findByRole('button', { name: 'Show citation 1' })).toBeVisible()
    expect(thread().getByText(/line one/)).toBeVisible()
  })

  it('sends with the icon button', async () => {
    const ask = vi.fn().mockResolvedValue(known)
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(box(), 'When are deploys frozen?')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(ask).toHaveBeenCalledExactlyOnceWith('When are deploys frozen?')
  })

  it('shows a searching indicator while pending, ignores duplicate sends and disables Send', async () => {
    let resolve!: (value: AnswerResponse) => void
    const ask = vi.fn(() => new Promise<AnswerResponse>(done => { resolve = done }))
    const user = userEvent.setup()
    render(<Ask ask={ask} library={library} />)
    await user.type(box(), 'When are deploys frozen?{Enter}')
    expect(screen.getByRole('status')).toHaveTextContent('Searching 2 repositories…')
    await user.type(box(), 'another question')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await user.keyboard('{Enter}')
    expect(ask).toHaveBeenCalledOnce()
    resolve(known)
    expect(await screen.findByRole('button', { name: 'Show citation 1' })).toBeVisible()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })

  it('keeps several question and answer pairs in order, each its own request', async () => {
    const ask = vi.fn().mockResolvedValueOnce(known).mockResolvedValueOnce(second)
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(box(), 'When are deploys frozen?{Enter}')
    await screen.findByText(/frozen on Fridays/)
    await user.type(box(), 'And how do I deploy again?{Enter}')
    await screen.findByText(/release checklist first/)
    expect(ask.mock.calls).toEqual([['When are deploys frozen?'], ['And how do I deploy again?']])
    const text = screen.getByRole('log', { name: 'Messages' }).textContent!
    const order = ['When are deploys frozen?', 'frozen on Fridays', 'And how do I deploy again?', 'release checklist first']
      .map(part => text.indexOf(part))
    expect(order.every(index => index >= 0)).toBe(true)
    expect([...order].sort((a, b) => a - b)).toEqual(order)
    expect(screen.getAllByText('Grounded answer')).toHaveLength(2)
  })

  it('posts each question to /api/ask with exactly the typed text', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(known))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<Ask />)
    await user.type(box(), 'When are deploys frozen?{Enter}')
    await screen.findByRole('button', { name: 'Show citation 1' })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/ask')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ question: 'When are deploys frozen?' })
  })

  it('New chat clears the whole conversation and restores the hero', async () => {
    const user = userEvent.setup()
    render(<Ask ask={vi.fn().mockResolvedValue(known)} suggestions={suggestions} />)
    await user.type(box(), 'When are deploys frozen?{Enter}')
    await screen.findByRole('button', { name: 'Show citation 1' })
    expect(screen.queryByRole('heading', { level: 2, name: 'Ask your team’s knowledge' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'New chat' }))
    expect(screen.queryByRole('log')).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Ask your team’s knowledge' })).toBeVisible()
    expect(box()).toHaveFocus()
  })

  it('discards the answer to a question that was cleared by New chat while pending', async () => {
    let resolve!: (value: AnswerResponse) => void
    const ask = vi.fn(() => new Promise<AnswerResponse>(done => { resolve = done }))
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(box(), 'q{Enter}')
    await user.click(screen.getByRole('button', { name: 'New chat' }))
    resolve(known)
    await Promise.resolve()
    expect(screen.queryByRole('log')).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
  })

  it('asks a suggestion when its card is clicked', async () => {
    const ask = vi.fn().mockResolvedValue(known)
    const user = userEvent.setup()
    render(<Ask ask={ask} suggestions={suggestions} />)
    await user.click(screen.getByRole('button', { name: /How is beta organised\?/ }))
    expect(ask).toHaveBeenCalledExactlyOnceWith('How is beta organised?')
    expect(await screen.findByRole('button', { name: 'Show citation 1' })).toBeVisible()
  })

  it('asks a preset question once, and again for a new key', async () => {
    const ask = vi.fn().mockResolvedValue(known)
    const { rerender } = render(<Ask ask={ask} preset={{ question: 'What is alpha?', key: 1 }} />)
    await screen.findByRole('button', { name: 'Show citation 1' })
    rerender(<Ask ask={ask} preset={{ question: 'What is alpha?', key: 1 }} />)
    expect(ask).toHaveBeenCalledOnce()
    rerender(<Ask ask={ask} preset={{ question: 'What is beta?', key: 2 }} />)
    await vi.waitFor(() => { expect(ask).toHaveBeenCalledTimes(2) })
    expect(ask).toHaveBeenLastCalledWith('What is beta?')
  })
})

describe('citation panel in the thread', () => {
  it('opens beside the thread when an answer arrives on a wide screen', async () => {
    stubViewport(true)
    const user = userEvent.setup()
    render(<Ask ask={vi.fn().mockResolvedValue(known)} />)
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    await user.type(box(), 'When are deploys frozen?{Enter}')
    const panel = await screen.findByRole('complementary', { name: 'Sources' })
    const card = within(panel).getByRole('article', { name: 'docs/deploy.md' })
    expect(within(card).getByRole('link', { name: /Open on GitHub/ })).toHaveAttribute('href', deployCitation.url)
    expect(card).toHaveAttribute('data-highlighted', 'false')
    expect(within(panel).getByText('When are deploys frozen?')).toBeVisible()
  })

  it('stays closed on a narrow screen until a chip is clicked, then highlights and focuses that card', async () => {
    stubViewport(false)
    const user = userEvent.setup()
    render(<Ask ask={vi.fn().mockResolvedValue(second)} />)
    await user.type(box(), 'How do I deploy?{Enter}')
    const chip = await screen.findByRole('button', { name: 'Show citation 2' })
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    await user.click(chip)
    const panel = screen.getByRole('complementary', { name: 'Sources' })
    const target = within(panel).getByRole('article', { name: longCitation.path })
    expect(target).toHaveAttribute('data-highlighted', 'true')
    expect(within(panel).getByRole('article', { name: 'docs/checklist.md' })).toHaveAttribute('data-highlighted', 'false')
    expect(target).toHaveFocus()
    expect(chip).toHaveAttribute('aria-controls', target.id)
    await user.click(screen.getByRole('button', { name: 'Show citation 1' }))
    expect(within(panel).getByRole('article', { name: 'docs/checklist.md' })).toHaveAttribute('data-highlighted', 'true')
    expect(target).toHaveAttribute('data-highlighted', 'false')
  })

  it('opens from the source count and the file pills, and closes with the button', async () => {
    stubViewport(false)
    const user = userEvent.setup()
    render(<Ask ask={vi.fn().mockResolvedValue(known)} />)
    await user.type(box(), 'q{Enter}')
    await user.click(await screen.findByRole('button', { name: '1 source' }))
    expect(screen.getByRole('complementary', { name: 'Sources' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Close sources' }))
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /deploy\.md/ }))
    expect(screen.getByRole('article', { name: 'docs/deploy.md' })).toHaveAttribute('data-highlighted', 'true')
  })

  it('shows the citations of whichever answer was clicked, even when numbers repeat', async () => {
    stubViewport(true)
    const ask = vi.fn().mockResolvedValueOnce(known).mockResolvedValueOnce(second)
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(box(), 'first{Enter}')
    await thread().findByText(/frozen on Fridays/)
    await user.type(box(), 'second{Enter}')
    await thread().findByText(/release checklist first/)
    const panel = screen.getByRole('complementary', { name: 'Sources' })
    expect(within(panel).getByText('second')).toBeVisible()
    expect(within(panel).getByRole('article', { name: 'docs/checklist.md' })).toBeVisible()
    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]!)
    expect(within(screen.getByRole('complementary', { name: 'Sources' })).getByText('first')).toBeVisible()
    expect(screen.getByRole('article', { name: 'docs/deploy.md' })).toHaveAttribute('data-highlighted', 'true')
    expect(screen.queryByRole('article', { name: 'docs/checklist.md' })).not.toBeInTheDocument()
  })

  it('closes with Escape from inside the panel', async () => {
    stubViewport(true)
    const user = userEvent.setup()
    render(<Ask ask={vi.fn().mockResolvedValue(known)} />)
    await user.type(box(), 'q{Enter}')
    const panel = await screen.findByRole('complementary', { name: 'Sources' })
    within(panel).getByRole('button', { name: 'Close sources' }).focus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
  })
})

describe('answers that are not answers', () => {
  it('shows the knowledge gap as an assistant message with no citations and no panel', async () => {
    stubViewport(true)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(unknown)))
    const user = userEvent.setup()
    render(<Ask />)
    await user.type(box(), 'What is the parental leave policy?{Enter}')
    expect(await screen.findByText(/couldn.t find this in the indexed sources/)).toBeVisible()
    expect(screen.getByText(/adding or re-indexing a source/)).toBeVisible()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('shows a failure in the thread and Retry asks the same question again in place', async () => {
    const ask = vi.fn().mockRejectedValueOnce(new Error('Answers are unavailable')).mockResolvedValue(known)
    const user = userEvent.setup()
    render(<Ask ask={ask} />)
    await user.type(box(), 'When are deploys frozen?{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('Answers are unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(ask).toHaveBeenCalledTimes(2)
    expect(ask).toHaveBeenLastCalledWith('When are deploys frozen?')
    expect(await screen.findByRole('button', { name: 'Show citation 1' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(thread().getAllByText(/When are deploys frozen\?/)).toHaveLength(1)
  })

  it('surfaces the server detail for a 503 and a generic message for a network failure', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ detail: 'Answers are unavailable' }, 503))
      .mockRejectedValueOnce(new TypeError('failed'))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<Ask />)
    await user.type(box(), 'q{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('Answers are unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Could not get an answer. Please try again.')).toBeVisible()
  })

  it('never renders source HTML as markup', async () => {
    const hostile: AnswerResponse = { ...known, answer: '<img src=x onerror="alert(1)"> [1]' }
    const user = userEvent.setup()
    const { container } = render(<Ask ask={() => Promise.resolve(hostile)} />)
    await user.type(box(), '<b>q</b>{Enter}')
    await screen.findByText(/<img src=x/)
    expect(container.querySelector('img, b')).toBeNull()
  })
})
