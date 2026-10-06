import type { NextConfig } from 'next';
const config: NextConfig = {
  agentRules: false,
  allowedDevOrigins: ['127.0.0.1'],
  experimental: { proxyTimeout: 300000, useTypeScriptCli: false },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.BACKEND_URL || 'http://127.0.0.1:8000'}/api/:path*` }];
  },
};
export default config;
