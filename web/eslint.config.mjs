import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Native SVG images preserve Figma export geometry; no raster optimization needed.
      '@next/next/no-img-element': 'off',
    },
  },
  globalIgnores(['.next/**', 'next-env.d.ts', 'playwright-report/**', 'test-results/**']),
]);
