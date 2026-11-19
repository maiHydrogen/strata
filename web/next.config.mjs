/** In development the Python API runs separately (npm run dev:api on port 8001); on Vercel,
 * web/api/*.py are serverless functions on the same domain, so no rewrite is needed. */
const nextConfig = {
  // Gemma on the free tier can take a minute per brief turn (with retries); the dev proxy's default
  // 30 s timeout cut those replies off. STAAD jobs return at once and are polled, so they are unaffected.
  experimental: { proxyTimeout: 180_000 },
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8001/api/:path*" }];
  },
};
export default nextConfig;
