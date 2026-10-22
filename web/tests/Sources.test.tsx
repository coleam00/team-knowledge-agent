import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Sources } from '../src/components/Sources'
import { sources } from './sources-fixtures'

afterEach(() => vi.unstubAllGlobals())

describe.each(['dark', 'light'])('Sources at 360px in %s theme (layout also checked in browser)', theme => {
  function setup(items = sources, onAdd = vi.fn(), onAsk = vi.fn()) {
    vi.stubGlobal('innerWidth', 360)
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query.includes(theme), media: query })))
    render(<Sources sources={items} onAdd={onAdd} onAsk={onAsk} />)
    return { user: userEvent.setup(), onAdd, onAsk }
  }

  it('renders every textual status, labelled metadata, and the actual failure', () => {
    setup()
    for (const source of sources) {
      const card = screen.getByRole('article', { name: `${source.owner}/${source.name}` })
      expect(within(card).getByText(source.status)).toBeVisible()
      expect(within(card).getByRole('button', { name: 'Ask about this repo' }).hasAttribute('disabled'))
        .toBe(source.status !== 'ready')
      expect(within(card).queryByRole('button', { name: /re-index/i })).not.toBeInTheDocument()
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

  it('asks about exactly the selected ready repository', async () => {
    const { user, onAsk } = setup()
    for (const source of sources) {
      const card = screen.getByRole('article', { name: `${source.owner}/${source.name}` })
      await user.click(within(card).getByRole('button', { name: 'Ask about this repo' }))
    }
    expect(onAsk).toHaveBeenCalledExactlyOnceWith(sources[2])
  })

  it('shows the library: stats, one card per repository and a quiet add tile', () => {
    setup()
    const stats = Object.fromEntries([...document.querySelectorAll('dl')[0]!.children].map(item => [
      item.querySelector('dt')!.textContent, item.querySelector('dd')!.textContent,
    ]))
    expect(stats).toEqual({ Repositories: '4', Ready: '1', 'Files indexed': '5', Chunks: '9' })
    expect(within(screen.getByRole('list', { name: 'Repositories' })).getAllByRole('article')).toHaveLength(4)
    expect(screen.getByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
  })

  it('opens the add panel from the header, adds once and closes it', async () => {
    const { user, onAdd } = setup()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add repository' }))
    const input = screen.getByRole('textbox', { name: 'Repository URL' })
    expect(input).toBeVisible()
    await user.type(input, 'https://github.com/team/new{Enter}')
    expect(onAdd).toHaveBeenCalledExactlyOnceWith('https://github.com/team/new')
    await waitFor(() => { expect(screen.queryByRole('form')).not.toBeInTheDocument() })
  })

  it('opens the add panel from the tile and cancels without adding', async () => {
    const { user, onAdd } = setup()
    await user.click(screen.getByRole('button', { name: /^Add a repository/ }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(onAdd).not.toHaveBeenCalled()
  })

  it('keeps the add panel open and shows the error when adding fails', async () => {
    const { user } = setup(sources, vi.fn().mockRejectedValue(new Error('This repository is already in the library.')))
    await user.click(screen.getByRole('button', { name: 'Add repository' }))
    await user.type(screen.getByRole('textbox', { name: 'Repository URL' }), 'https://github.com/team/handbook{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('already in the library')
    expect(screen.getByRole('form')).toBeVisible()
  })
})
