import { defineConfig } from 'vitest/config';

export default defineConfig({ test: {
  include: ['tests/**/*.test.ts', 'web/src/**/*.test.ts'],
  exclude: process.env.FIRESTORE_EMULATOR_HOST ? [] : ['tests/firebase-rules.test.ts'],
  testTimeout: 20000,
} });
