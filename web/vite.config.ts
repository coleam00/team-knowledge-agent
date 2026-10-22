import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  // Dev only: `npm run dev` proxies the API so the UI can be previewed against a running backend.
  server: { proxy: { '/api': process.env.TKA_API_URL ?? 'http://127.0.0.1:8000' } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    clearMocks: true,
  },
})
