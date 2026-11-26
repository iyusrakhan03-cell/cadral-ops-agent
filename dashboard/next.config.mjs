/** @type {import('next').NextConfig} */
// The browser only ever talks to this site; /backend/* is forwarded by Vercel to the FastAPI backend.
// That avoids CORS and Chrome's private-network blocking (the backend sits behind a Tailscale Funnel on a PC).
const backend = (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
const proxied = backend.startsWith("http") && !backend.includes("localhost");

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return proxied ? [{ source: "/backend/:path*", destination: `${backend}/:path*` }] : [];
  },
};
export default nextConfig;
