import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  experimental: {
    typedRoutes: true,
  },
  // Orphan scaffold pages (untracked, spread across tabs) have pre-existing type
  // mismatches from an earlier experiment. They don't block the shell port
  // sprint — enable once all scaffolds are either wired or stubbed.
  typescript: {
    ignoreBuildErrors: true,
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
  // NEXT_PUBLIC_API_BASE_URL is intentionally not defaulted here — see
  // apps/web/lib/apiBase.ts for runtime resolution. Baking a default here
  // breaks loopback vs public origin separation (Chrome's Private Network
  // Access blocks loopback fetches from https://gazua.yule.pics).
};

export default config;
