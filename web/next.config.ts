import type { NextConfig } from 'next';
import { cutoverAfterFiles, cutoverBeforeFiles, cutoverRedirects } from './lib/cutover.ts';
const config: NextConfig = {
  distDir: process.env.AUDLI_BROWSER_TEST === '1' ? '.next-browser-tests' : '.next',
  agentRules: false,
  allowedDevOrigins: ['127.0.0.1'],
  experimental: { proxyTimeout: 300000, useTypeScriptCli: false },
  async headers() {
    return ['/auth/recovery', '/forgot-password', '/auth/callback'].map(source => ({ source, headers: [
      { key: 'Referrer-Policy', value: 'no-referrer' },
      { key: 'Cache-Control', value: 'no-store' },
      { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
    ] }));
  },
  async redirects() {
    return cutoverRedirects();       // empty unless both Audli 2.0 flags are on
  },
  async rewrites() {
    return {
      beforeFiles: cutoverBeforeFiles(),     // "/" -> the 2.0 home (only when both flags are on)
      afterFiles: [...cutoverAfterFiles(), { source: '/api/:path*', destination: `${process.env.BACKEND_URL || 'http://127.0.0.1:8000'}/api/:path*` }],
      fallback: [],
    };
  },
};
export default config;
