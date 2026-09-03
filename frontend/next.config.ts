import type { NextConfig } from "next";
import { parseApiOrigin } from "./lib/config";

// Missing configuration is visible in the console; malformed values fail startup.
parseApiOrigin(process.env.NEXT_PUBLIC_API_BASE_URL);

const nextConfig: NextConfig = {
  agentRules: false,
  poweredByHeader: false,
  // The dev tools badge is pinned to the bottom-left corner, which is exactly where the
  // sidebar says every recorded action is a simulation. That sentence has to stay legible
  // while the console is being demonstrated, and this is development-only chrome.
  devIndicators: false,
};

export default nextConfig;
