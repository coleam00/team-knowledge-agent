import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Answer, CitationCard } from '../src/components/Answer'
import { citedAnswer, deployCitation, literalText, longCitation } from './citations-fixtures'

afterEach(() => { vi.restoreAllMocks() })

describe('answer', () => {
  it('shows the grounded header, source count and one chip per known marker', () => {
    render(<Answer response={citedAnswer} idPrefix="t" />)
    expect(screen.getByText('Grounded answer')).toBeVisible()
    expect(screen.getByRole('button', { name: '2 sources' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Show citation 1' })).toHaveTextContent('[1]')
    expect(screen.getByRole('button', { name: 'Show citation 2' })).toHaveAttribute('aria-controls', 't-citation-2')
    // An unmatched marker stays plain text.
    expect(screen.getByText(/stays plain text: \[99\]/)).toBeVisible()
    expect(screen.getByRole('list', { name: 'Cited sources' }).querySelectorAll('li')).toHaveLength(2)
  })

  it('reports the exact citation for chips and pills, by number rather than array position', async () => {
    const user = userEvent.setup()
    const onCite = vi.fn()
    const onOpenSources = vi.fn()
    // Reverse order to prove markers match n, not the citation array index.
    render(<Answer response={{ ...citedAnswer, citations: [longCitation, deployCitation] }} onCite={onCite} onOpenSources={onOpenSources} selectedN={2} />)
    await user.tab()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Show citation 1' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onCite).toHaveBeenLastCalledWith(deployCitation)
    screen.getByRole('button', { name: 'Show citation 2' }).focus()
    await user.keyboard(' ')
    expect(onCite).toHaveBeenLastCalledWith(longCitation)
    expect(screen.getByRole('button', { name: 'Show citation 2' })).toHaveAttribute('data-active', 'true')
    expect(screen.getByRole('button', { name: 'Show citation 1' })).toHaveAttribute('data-active', 'false')
    await user.click(screen.getByRole('button', { name: /deploy\.md/ }))
    expect(onCite).toHaveBeenLastCalledWith(deployCitation)
    await user.click(screen.getByRole('button', { name: '2 sources' }))
    expect(onOpenSources).toHaveBeenCalledOnce()
  })

  it('copies the answer text', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<Answer response={citedAnswer} />)
    await user.click(screen.getByRole('button', { name: 'Copy answer' }))
    expect(writeText).toHaveBeenCalledWith(citedAnswer.answer)
    expect(await screen.findByRole('button', { name: 'Answer copied' })).toBeVisible()
  })

  it('uses the supplied URL verbatim and exposes the repo, path, range and new-tab behavior', () => {
    render(<CitationCard citation={deployCitation} />)
    expect(screen.getByText('tka-fixtures/handbook')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'docs/deploy.md' })).toBeVisible()
    expect(screen.getByText('L14-L18')).toBeVisible()
    expect(screen.getByRole('link', { name: /Open on GitHub/ })).toHaveAttribute('href',
      'https://github.com/tka-fixtures/handbook/blob/C/docs/deploy.md#L14-L18')
    expect(screen.getByRole('link')).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('does not rebuild a supplied URL from other citation fields', () => {
    const url = 'https://github.com/tka-fixtures/handbook/blob/authoritative/docs/deploy.md#L7-L9'
    render(<CitationCard citation={{ ...deployCitation, url }} />)
    expect(screen.getByRole('link')).toHaveAttribute('href', url)
  })

  it.each([
    { snippet: 'one\ntwo\nthree\nfour\nfive\nsix\nseven', count: 6 },
    { snippet: 'one\r\ntwo\r\nthree\r\n', count: 3 },
    { snippet: 'one\ntwo', count: 2 },
    { snippet: 'one\n', count: 1 },
    { snippet: 'one\n\n', count: 2 },
    { snippet: '', count: 0 },
  ])('shows only $count actual rows for a snippet', ({ snippet, count }) => {
    render(<CitationCard citation={{ ...longCitation, snippet }} />)
    const list = screen.getByRole('list', { name: 'Source lines' })
    const rows = within(list).queryAllByRole('listitem')
    expect(rows).toHaveLength(count)
    expect(list).toHaveAttribute('start', '80')
    rows.forEach((row, index) => {
      expect(within(row).getByText(String(80 + index))).toBeVisible()
      expect(row.querySelector('code')!.textContent).toBe(snippet.split(/\r?\n/)[index])
    })
  })

  it('never numbers snippet rows beyond the API range', () => {
    render(<CitationCard citation={{ ...deployCitation, end_line: 15, snippet: 'one\ntwo\nextra' }} />)
    expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.queryByText('extra')).not.toBeInTheDocument()
  })

  it('preserves the beginning and filename of long paths and exposes the complete path', () => {
    render(<CitationCard citation={longCitation} />)
    const path = screen.getByRole('heading', { name: longCitation.path })
    expect(path).toHaveAttribute('title', longCitation.path)
    expect(path.textContent).toMatch(/^docs\/platform\/oper…\/emergency-deployment-procedure\.md$/)
  })

  it('renders hostile content as literal text and leaves unmatched numeric markers unlinked', () => {
    const { container } = render(<Answer response={{
      known: true,
      answer: `${literalText} [1] [2] [99] [01]`,
      citations: [{ ...deployCitation, snippet: literalText, repo: literalText, path: literalText }],
    }} />)
    expect(container.querySelector('script, img, [onerror]')).toBeNull()
    expect(container.textContent).toContain('<script>window.citationExecuted = true</script>')
    expect(container.textContent).toContain('[2] [99] [01]')
    expect(screen.getAllByRole('button', { name: /Show citation/ })).toHaveLength(1)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders formatted text: paragraphs, lists, inline code and bold', () => {
    const { container } = render(<Answer response={{
      known: true,
      answer: 'Start with **the harness**.\n1. Run `npm ci`\n2. Run `npm test` [1]\n- first\n- second',
      citations: [deployCitation],
    }} />)
    expect(container.querySelector('strong')).toHaveTextContent('the harness')
    expect(container.querySelectorAll('ol li')).toHaveLength(2)
    expect(container.querySelectorAll('ul:not([aria-label]) li')).toHaveLength(2)
    expect(container.querySelectorAll('code')).toHaveLength(2)
  })

  it.each(['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'https://github.com.evil.test/blob/C', 'https://user:pass@github.com/a/b', '/relative'])('does not make an unsafe API URL executable: %s', url => {
    render(<CitationCard citation={{ ...deployCitation, url }} />)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText('GitHub link unavailable.')).toBeVisible()
  })

  it('renders the honest gap with no citations even if the response carries stale content', () => {
    // @ts-expect-error Malformed external data violates the generated response contract.
    render(<Answer response={{ ...citedAnswer, known: false }} />)
    expect(screen.getByRole('status')).toHaveTextContent('I couldn’t find this in the indexed sources.')
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
