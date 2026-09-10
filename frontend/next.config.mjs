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
const LOCAL_API = "http://127.0.0.1:8000";
const API_ORIGIN = process.env.ORCA_API_ORIGIN ?? LOCAL_API;

// The loopback default is a DEVELOPMENT default: it assumes uvicorn is running
// beside Next on the same machine. On a serverless host there is no such
// neighbour -- 127.0.0.1:8000 is the function's own loopback, where nothing
// listens -- so every /api/* call returns a gateway error while the map itself
// loads fine from OSM tiles.
//
// That specific failure is the one this project treats as unacceptable: a page
// that looks live but carries no hazard data. Warn loudly at build time rather
// than let it be discovered by a judge clicking a silent panel.
if (process.env.VERCEL && !process.env.ORCA_API_ORIGIN) {
  console.warn(
    "\n[orca] WARNING: building on Vercel with no ORCA_API_ORIGIN set.\n" +
      "[orca] /api/* will proxy to " + LOCAL_API + ", which does not exist here,\n" +
      "[orca] so the map will render with every data panel erroring.\n" +
      "[orca] Set ORCA_API_ORIGIN to the public URL of the FastAPI backend.\n"
  );
}

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
