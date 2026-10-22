import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Answer, CitationCard } from '../src/components/Answer'
import { citedAnswer, deployCitation, literalText, longCitation } from './citations-fixtures'

afterEach(() => { vi.restoreAllMocks() })

describe('answer citations', () => {
  it('activates the exact matching card with Enter and Space, then tabs to its link', async () => {
    const user = userEvent.setup()
    const scroll = vi.fn()
    HTMLElement.prototype.scrollIntoView = scroll
    // Reverse order to prove markers match n, not the citation array index.
    render(<Answer response={{ ...citedAnswer, citations: [longCitation, deployCitation] }} />)
    const first = screen.getByRole('article', { name: deployCitation.path })
    const second = screen.getByRole('article', { name: longCitation.path })
    const chip1 = screen.getByRole('button', { name: 'Show citation 1' })
    const chip2 = screen.getByRole('button', { name: 'Show citation 2' })
    await user.tab()
    expect(chip1).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(first).toHaveFocus()
    expect(chip1).toHaveAttribute('aria-controls', first.id)
    expect(first).toHaveAttribute('data-highlighted', 'true')
    expect(second).toHaveAttribute('data-highlighted', 'false')
    expect(scroll.mock.instances[0]).toBe(first)
    expect(scroll).toHaveBeenLastCalledWith({ block: 'nearest', behavior: 'instant' })
    await user.tab()
    expect(within(first).getByRole('link')).toHaveFocus()
    chip2.focus()
    await user.keyboard(' ')
    expect(second).toHaveFocus()
    expect(second).toHaveAttribute('data-highlighted', 'true')
    expect(first).toHaveAttribute('data-highlighted', 'false')
    expect(scroll.mock.instances[1]).toBe(second)
  })

  it('keeps separate answers isolated, even when they cite the same number', async () => {
    const user = userEvent.setup()
    HTMLElement.prototype.scrollIntoView = vi.fn()
    render(<><Answer response={citedAnswer} /><Answer response={citedAnswer} /></>)
    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[1]!)
    const cards = screen.getAllByRole('article', { name: deployCitation.path })
    expect(cards[1]).toHaveFocus()
    expect(cards[0]).toHaveAttribute('data-highlighted', 'false')
    expect(cards[1]).toHaveAttribute('data-highlighted', 'true')
    expect(cards[0]!.id).not.toBe(cards[1]!.id)
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
    expect(container.textContent).toContain(literalText)
    expect(container.textContent).toContain('[2] [99] [01]')
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.getAllByRole('link')).toHaveLength(1)
    expect(within(screen.getByRole('list', { name: 'Source lines' })).getAllByRole('listitem')).toHaveLength(2)
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
