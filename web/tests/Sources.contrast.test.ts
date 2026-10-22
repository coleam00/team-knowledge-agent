import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const [dark, light] = readFileSync(resolve('src/styles/tokens.css'), 'utf8').split(":root[data-theme='light']")
function tokens(css: string) {
  return Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[\da-f]{6});/gi)].map(match => [match[1]!, match[2]!]))
}
function luminance(hex: string) {
  const values = hex.slice(1).match(/../g)!.map(part => {
    const value = parseInt(part, 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return values[0]! * 0.2126 + values[1]! * 0.7152 + values[2]! * 0.0722
}

describe.each(['dark', 'light'])('source text contrast in %s theme', theme => {
  it('keeps all source status, error, metadata and body text at least 4.5:1 on their surface', () => {
    const palette = { ...tokens(dark!), ...(theme === 'light' ? tokens(light!) : {}) }
    for (const name of ['success', 'danger', 'accent', 'text', 'text-muted']) {
      const values = [luminance(palette[name]!), luminance(palette.surface!)].sort((a, b) => b - a)
      expect((values[0]! + 0.05) / (values[1]! + 0.05), name).toBeGreaterThanOrEqual(4.5)
    }
  })
})
