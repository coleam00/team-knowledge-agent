// Build actual components for browser verification on the running backend.
// This separate entry is never part of npm run build or the shipped app.
import { fileURLToPath, URL } from 'node:url'
import { build } from 'vite'
import react from '@vitejs/plugin-react'

await build({
  configFile: false,
  root: fileURLToPath(new URL('../', import.meta.url)),
  base: './',
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL('../../app/static/assets/verification', import.meta.url)),
    emptyOutDir: true,
    rolldownOptions: {
      input: fileURLToPath(new URL('./states.html', import.meta.url)),
    },
  },
})
