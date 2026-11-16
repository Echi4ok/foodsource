import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'

const port = 8775
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: 'tests',
  use: { baseURL, viewport: { width: 390, height: 844 } },
  webServer: {
    command: `uv run --project backend --locked python -X utf8 -m scripts.demo --port ${port}`,
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    url: `${baseURL}/api/health`,
  },
})
