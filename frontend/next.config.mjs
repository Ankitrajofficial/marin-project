/** @type {import('next').NextConfig} */

// Single origin. The browser only ever talks to this Next server: pages and
// /api/* come from the same scheme+host+port, so there is one URL to open, no
// CORS preflight, and no API host baked into the client bundle.
//
// Read at server start and used only by the proxy below. Deliberately NOT
// NEXT_PUBLIC_*: that prefix would inline the backend address into JavaScript
// shipped to the browser, which is exactly what this setup removes.
//
// 127.0.0.1, not localhost: uvicorn binds 127.0.0.1, while Node may resolve
// "localhost" to ::1 first and fail the proxy with ECONNREFUSED on a backend
// that is plainly running.
const API_ORIGIN = process.env.ORCA_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
