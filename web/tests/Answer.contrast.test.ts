import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const [dark, light] = readFileSync(resolve('src/styles/tokens.css'), 'utf8').split('@media (prefers-color-scheme: light)')
function tokens(css: string) {
  return Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[\da-f]{6});/gi)].map(match => [match[1]!, match[2]!]))
}
function luminance(hex: string) {
  const rgb = hex.slice(1).match(/../g)!.map(part => {
    const value = parseInt(part, 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return rgb[0]! * 0.2126 + rgb[1]! * 0.7152 + rgb[2]! * 0.0722
}

describe.each(['dark', 'light'])('citations in %s theme', theme => {
  it('keeps body, muted metadata, numbers, chips and links at AA contrast', () => {
    const palette = { ...tokens(dark!), ...(theme === 'light' ? tokens(light!) : {}) }
    for (const background of ['background', 'surface']) {
      for (const foreground of ['text', 'text-muted', 'accent']) {
        const values = [luminance(palette[foreground]!), luminance(palette[background]!)].sort((a, b) => b - a)
        expect((values[0]! + 0.05) / (values[1]! + 0.05), `${foreground}/${background}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
})
