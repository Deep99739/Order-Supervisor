import { parseApiOrigin } from "./config";
import { isReadiness, type Readiness } from "./setup-contracts";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: "configuration" | "network" | "response",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function getReadiness(signal: AbortSignal): Promise<Readiness> {
  let origin: string | null;
  try {
    origin = parseApiOrigin(process.env.NEXT_PUBLIC_API_BASE_URL);
  } catch {
    throw new ApiError(
      "NEXT_PUBLIC_API_BASE_URL must be one HTTP(S) origin without credentials or a path.",
      "configuration",
    );
  }
  if (!origin) {
    throw new ApiError(
      "The API address is missing. Set NEXT_PUBLIC_API_BASE_URL in the frontend environment and restart the console.",
      "configuration",
    );
  }

  let response: Response;
  try {
    response = await fetch(`${origin}/readyz`, {
      cache: "no-store",
      signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]),
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError(
      "The API could not be reached. Check that the local service is running and allows this console, then try again.",
      "network",
    );
  }

  // A dependency failure intentionally returns a valid readiness body with 503.
  if (response.status !== 200 && response.status !== 503) {
    throw new ApiError(
      `The service check returned an unexpected response (${response.status}).`,
      "response",
    );
  }

  const data: unknown = await response.json().catch(() => null);
  if (!isReadiness(data)) {
    throw new ApiError(
      "The API returned an unreadable service check. Verify that the console and API versions match.",
      "response",
    );
  }
  return data;
}
