import type { NextConfig } from "next";

/**
 * The FastAPI backend owns every route under /api. The browser only ever talks
 * to this Next server, so the app keeps the same-origin security model that
 * app/api.py enforces (host check, Origin check, `connect-src 'self'`).
 *
 * Override with MOODLEAI_BACKEND if the backend runs on another loopback port.
 */
const backend = (process.env.MOODLEAI_BACKEND ?? "http://127.0.0.1:8765").replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  turbopack: {
    root: process.cwd(),
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
