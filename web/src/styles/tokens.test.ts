import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve('src/styles/tokens.css'), 'utf8')
const [dark, light] = css.split(":root[data-theme='light']")
const palettes = {
  dark: { background: '#0e1116', surface: '#161b22', raised: '#1f2630', border: '#2d3440', text: '#e6edf3', 'text-muted': '#9aa4b2', accent: '#6cb6ff', success: '#3fb950', warning: '#d29922', danger: '#f85149' },
  light: { background: '#ffffff', surface: '#f6f8fa', border: '#d0d7de', text: '#1f2328', 'text-muted': '#59636e', accent: '#0969da' },
}

function luminance(hex: string) {
  const channels = hex.slice(1).match(/../g)!.map(channel => {
    const value = parseInt(channel, 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return channels[0]! * 0.2126 + channels[1]! * 0.7152 + channels[2]! * 0.0722
}

describe('design tokens', () => {
  it('defines every exact PRD dark and light colour', () => {
    for (const [theme, palette] of Object.entries(palettes)) {
      for (const [name, colour] of Object.entries(palette)) {
        expect(theme === 'dark' ? dark : light).toContain(`--${name}: ${colour};`)
      }
    }
  })

  it('keeps body, muted citation metadata and accent links at AA contrast', () => {
    for (const palette of Object.values(palettes)) {
      const backgrounds = [palette.background, palette.surface, 'raised' in palette ? palette.raised : palette.background]
      for (const background of backgrounds) {
        for (const foreground of [palette.text, palette['text-muted'], palette.accent]) {
          const values = [luminance(background), luminance(foreground)].sort((a, b) => b - a)
          expect((values[0]! + 0.05) / (values[1]! + 0.05), `${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5)
        }
      }
    }
  })

  it('requires token references for every component colour declaration', () => {
    const files = readdirSync(resolve('src'), { recursive: true }).map(String).filter(path => path.endsWith('.module.css'))
    expect(files.length).toBeGreaterThan(0)
    for (const file of files) {
      const component = readFileSync(resolve('src', file), 'utf8')
      expect(component).not.toMatch(/#[\da-f]{3,8}\b|\b(?:rgb|hsl|hwb|lab|lch|oklab|oklch)\(/i)
      for (const declaration of component.matchAll(/(?:color|background|border(?:-color|-right|-bottom)?|outline)\s*:\s*([^;]+);/g)) {
        if (declaration[1]!.trim() !== '0') expect(declaration[1]).toContain('var(--')
      }
    }
  })
})
