/** In development the Python API runs separately (npm run dev:api on port 8001); on Vercel,
 * web/api/*.py are serverless functions on the same domain, so no rewrite is needed. */
const nextConfig = {
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8001/api/:path*" }];
  },
};
export default nextConfig;
