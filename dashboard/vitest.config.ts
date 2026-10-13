import { defineConfig } from 'vitest/config'

// Pure-logic unit tests only (node environment, no DOM): coverage is scoped to
// the modules exercised here so the published badge reflects real coverage.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],
      reportsDirectory: 'coverage',
      include: ['src/lib/format.ts', 'src/lib/api.ts'],
    },
  },
})
