import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

beforeEach(() => {
  vi.stubGlobal('fetch', () => { throw new Error('Network access is forbidden in tests') })
  vi.stubGlobal('XMLHttpRequest', class {
    constructor() { throw new Error('Network access is forbidden in tests') }
  })
  vi.stubGlobal('WebSocket', class {
    constructor() { throw new Error('Network access is forbidden in tests') }
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
