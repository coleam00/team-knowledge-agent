import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Sources } from '../src/components/Sources'
import { sources } from './sources-fixtures'

afterEach(() => vi.unstubAllGlobals())

describe.each(['dark', 'light'])('Sources at 360px in %s theme (layout also checked in browser)', theme => {
  function setup(items = sources, onAdd = vi.fn(), onReindex = vi.fn()) {
    vi.stubGlobal('innerWidth', 360)
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query.includes(theme), media: query })))
    render(<Sources sources={items} onAdd={onAdd} onReindex={onReindex} />)
    return { user: userEvent.setup(), onAdd, onReindex }
  }

  it('renders every textual status, labelled metadata, and the actual failure', () => {
    setup()
    for (const source of sources) {
      const card = screen.getByRole('article', { name: `${source.owner}/${source.name}` })
      expect(within(card).getByText(source.status)).toBeVisible()
      expect(within(card).getByRole('button', { name: 'Re-index' }).hasAttribute('disabled'))
        .toBe(source.status === 'queued' || source.status === 'indexing')
    }
    const ready = screen.getByRole('article', { name: 'team/handbook' })
    const metadata = Object.fromEntries([...ready.querySelectorAll('dl > div')].map(item => [
      item.querySelector('dt')!.textContent, item.querySelector('dd')!.textContent,
    ]))
    expect(metadata).toEqual({ 'Indexed commit': '0123456', 'Last indexed': '20 Oct 2026, 14:00 UTC', Files: '5', Chunks: '9' })
    expect(ready.querySelector('time')).toHaveAttribute('datetime', '2026-10-20T14:00:00Z')
    expect(screen.getByText(/Indexing failed:/)).toHaveTextContent(sources[3]!.error!)
    expect(screen.getAllByText('Not indexed yet')).toHaveLength(6)
  })

  it('adds once with the entered URL using keyboard submission from the empty state', async () => {
    const { user, onAdd } = setup([])
    expect(screen.getByRole('heading', { name: 'No sources yet' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Add repository' })).toBeVisible()
    await user.tab()
    expect(screen.getByRole('textbox', { name: 'Repository URL' })).toHaveFocus()
    await user.keyboard('https://github.com/team/handbook{Enter}')
    expect(onAdd).toHaveBeenCalledExactlyOnceWith('https://github.com/team/handbook')
  })

  it.each(['', 'https://gitlab.com/team/handbook', 'https://github.com/team/handbook/blob/main/README.md', 'https://github.com/team/handbook?token=x'])('retains invalid input %s and supports correction', async value => {
    const { user, onAdd } = setup([])
    const input = screen.getByRole('textbox', { name: 'Repository URL' })
    if (value) await user.type(input, value)
    await user.click(screen.getByRole('button', { name: 'Add repository' }))
    expect(input).toHaveValue(value)
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription(expect.stringContaining('Enter a public GitHub repository URL'))
    expect(onAdd).not.toHaveBeenCalled()
    await user.clear(input)
    await user.type(input, 'https://github.com/team/handbook.git/')
    await user.keyboard('{Enter}')
    expect(onAdd).toHaveBeenCalledExactlyOnceWith('https://github.com/team/handbook.git/')
  })

  it('retains pending input, prevents duplicate submission, and allows retry after a callback failure', async () => {
    let reject!: (error: Error) => void
    const onAdd = vi.fn().mockImplementationOnce(() => new Promise<void>((_, fail) => { reject = fail }))
    const { user } = setup([], onAdd)
    const input = screen.getByRole('textbox', { name: 'Repository URL' })
    await user.type(input, 'https://github.com/team/handbook{Enter}')
    expect(input).toHaveValue('https://github.com/team/handbook')
    expect(input).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: 'Adding repository…' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Adding this repository')
    fireEvent.submit(screen.getByRole('form'))
    expect(onAdd).toHaveBeenCalledOnce()
    await act(async () => reject(new Error('This repository is already a source.')))
    expect(screen.getByRole('alert')).toHaveTextContent('This repository is already a source.')
    expect(input).toHaveValue('https://github.com/team/handbook')
    await user.click(screen.getByRole('button', { name: 'Add repository' }))
    expect(onAdd).toHaveBeenCalledTimes(2)
  })

  it('only re-indexes ready/failed and passes precisely the selected source id', async () => {
    const { user, onReindex } = setup()
    for (const source of sources) {
      const card = screen.getByRole('article', { name: `${source.owner}/${source.name}` })
      const button = within(card).getByRole('button', { name: 'Re-index' })
      await user.click(button)
    }
    expect(onReindex.mock.calls).toEqual([['source-ready'], ['source-failed']])
  })

  it('supports keyboard re-index, blocks pending repeats, and presents retryable failure', async () => {
    let reject!: (error: Error) => void
    const onReindex = vi.fn().mockImplementationOnce(() => new Promise<void>((_, fail) => { reject = fail }))
    const { user } = setup([sources[2]!], vi.fn(), onReindex)
    await user.tab()
    expect(screen.getByRole('button', { name: 'Re-index' })).toHaveFocus()
    await user.keyboard('{Enter}{Enter}')
    expect(onReindex).toHaveBeenCalledExactlyOnceWith('source-ready')
    expect(screen.getByRole('button', { name: 'Queuing re-index…' })).toBeDisabled()
    await act(async () => reject(new Error('The server could not queue the job.')))
    expect(screen.getByRole('alert')).toHaveTextContent('The server could not queue the job.')
    await user.click(screen.getByRole('button', { name: 'Re-index' }))
    expect(onReindex).toHaveBeenCalledTimes(2)
  })
})
