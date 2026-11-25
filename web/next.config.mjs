/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Set STATIC_EXPORT=1 to produce a fully static site in ./out
  // (all API calls are client-side, so no server runtime is needed).
  ...(process.env.STATIC_EXPORT === "1" ? { output: "export" } : {}),
};
export default nextConfig;
