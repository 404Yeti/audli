import type { NextConfig } from 'next';
const config: NextConfig = {
  distDir: process.env.AUDLI_BROWSER_TEST === '1' ? '.next-browser-tests' : '.next',
  agentRules: false,
  allowedDevOrigins: ['127.0.0.1'],
  experimental: { proxyTimeout: 300000, useTypeScriptCli: false },
  async headers() {
    return ['/auth/recovery', '/forgot-password'].map(source => ({ source, headers: [
      { key: 'Referrer-Policy', value: 'no-referrer' },
      { key: 'Cache-Control', value: 'no-store' },
      { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
    ] }));
  },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.BACKEND_URL || 'http://127.0.0.1:8000'}/api/:path*` }];
  },
};
export default config;
