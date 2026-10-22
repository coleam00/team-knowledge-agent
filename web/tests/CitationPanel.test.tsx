import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CitationPanel } from '../src/components/CitationPanel'
import { deployCitation, literalText, longCitation } from './citations-fixtures'

afterEach(() => { vi.restoreAllMocks() })

function setup(selectedN: number | null = null, focusToken = 0, onClose = vi.fn()) {
  const scroll = vi.fn()
  HTMLElement.prototype.scrollIntoView = scroll
  const props = { citations: [deployCitation, longCitation], question: 'When are deploys frozen?', idPrefix: 'p', onClose }
  const view = render(<CitationPanel {...props} selectedN={selectedN} focusToken={focusToken} />)
  return { scroll, onClose, props, view }
}

describe('citation panel', () => {
  it('lists every citation card with a summary and the question it belongs to', () => {
    setup()
    const panel = screen.getByRole('complementary', { name: 'Sources' })
    expect(within(panel).getByText('2 citations · 2 files')).toBeVisible()
    expect(within(panel).getByText('When are deploys frozen?')).toBeVisible()
    expect(within(panel).getAllByRole('article')).toHaveLength(2)
    expect(screen.getByRole('article', { name: deployCitation.path }).id).toBe('p-citation-1')
  })

  it('highlights, scrolls to and focuses the selected card when a chip was clicked', () => {
    const { scroll, props, view } = setup(2, 1)
    const second = screen.getByRole('article', { name: longCitation.path })
    expect(second).toHaveAttribute('data-highlighted', 'true')
    expect(screen.getByRole('article', { name: deployCitation.path })).toHaveAttribute('data-highlighted', 'false')
    expect(second).toHaveFocus()
    expect(scroll.mock.instances[0]).toBe(second)
    expect(scroll).toHaveBeenLastCalledWith({ block: 'nearest', behavior: 'auto' })
    // Selecting the same citation again (new token) re-scrolls it.
    view.rerender(<CitationPanel {...props} selectedN={2} focusToken={2} />)
    expect(scroll).toHaveBeenCalledTimes(2)
  })

  it('scrolls without stealing focus when the selection was not a click', () => {
    setup(1, 0)
    expect(screen.getByRole('article', { name: deployCitation.path })).not.toHaveFocus()
  })

  it('closes with the button and with Escape from inside the panel', async () => {
    const user = userEvent.setup()
    const { onClose } = setup()
    await user.click(screen.getByRole('button', { name: 'Close sources' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    screen.getAllByRole('link', { name: /Open on GitHub/ })[0]!.focus()
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('renders hostile snippet text literally', () => {
    HTMLElement.prototype.scrollIntoView = vi.fn()
    const { container } = render(<CitationPanel citations={[{ ...deployCitation, snippet: literalText }]} question="q" idPrefix="x"
      selectedN={null} focusToken={0} onClose={vi.fn()} />)
    expect(container.querySelector('script, img, [onerror]')).toBeNull()
    expect(container.textContent).toContain('<script>')
  })
})
