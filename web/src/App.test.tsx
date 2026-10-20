import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('application foundation', () => {
  it('renders the application name and explains what is available', () => {
    render(<App />)
    const main = within(screen.getByRole('main'))
    expect(main.getByRole('heading', { level: 1, name: 'Team Knowledge Agent' })).toBeVisible()
    expect(main.getByText(/coming in later releases/)).toBeVisible()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
