import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EmptySources, ErrorState, LoadingState, UnknownAnswer } from './States'

describe('shared states', () => {
  it('announces loading and renders a decorative skeleton', () => {
    render(<LoadingState label="Loading sources…" />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading sources…')
    expect(screen.getByRole('status').querySelector('[aria-hidden="true"]')?.children).toHaveLength(3)
  })

  it('announces the actual error message and retries by keyboard', async () => {
    const onRetry = vi.fn()
    const user = userEvent.setup()
    render(<ErrorState message="The repository could not be reached." onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('The repository could not be reached.')
    await user.tab()
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('renders and operates the supplied add-form slot', async () => {
    const submit = vi.fn()
    const user = userEvent.setup()
    render(<EmptySources addForm={
      <form onSubmit={event => { event.preventDefault(); submit() }}>
        <label>Repository URL<input name="url" type="url" required /></label>
        <button type="submit">Add repository</button>
      </form>
    } />)
    expect(screen.getByRole('heading', { name: 'No sources yet' })).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: 'Repository URL' }), 'https://github.com/acme/handbook')
    await user.click(screen.getByRole('button', { name: 'Add repository' }))
    expect(submit).toHaveBeenCalledOnce()
  })

  it('explains an unknown answer and how to improve the sources', () => {
    render(<UnknownAnswer />)
    expect(screen.getByRole('status')).toHaveTextContent('I couldn’t find this in the indexed sources.')
    expect(screen.getByRole('status')).toHaveTextContent('adding or re-indexing a source')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
