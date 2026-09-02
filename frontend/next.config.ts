import type { NextConfig } from "next";
import { parseApiOrigin } from "./lib/config";

// Missing configuration is visible in the console; malformed values fail startup.
parseApiOrigin(process.env.NEXT_PUBLIC_API_BASE_URL);

const nextConfig: NextConfig = {
  agentRules: false,
  poweredByHeader: false,
};

export default nextConfig;
