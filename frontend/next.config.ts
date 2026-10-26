import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Hide the Next.js dev-tools "N" badge and its route/Turbopack menu in `next dev`. Build and runtime errors still
  // surface through the Next.js issues badge and error overlay; production builds never show the badge.
  devIndicators: false,
};

export default nextConfig;
