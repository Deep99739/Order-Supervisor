import { parseApiOrigin } from "./config";
import { isReadiness, type Readiness } from "./setup-contracts";
import type {
  ActivityCategory,
  ActivityPage,
  CommandAcknowledgement,
  ControlCommand,
  CreateRunRequest,
  EventCommand,
  InstructionCommand,
  ReviewCommand,
  RunCreated,
  RunPage,
  RunView,
  SupervisorDraft,
  SupervisorList,
  SupervisorRecord,
  SupervisorUpdate,
} from "./contracts";

/**
 * `kind` separates a problem the operator can fix (configuration), a problem with the
 * connection (network), and a refusal the API described itself (response). `code` and
 * `fieldDetails` come from the API's own error body when there is one.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: "configuration" | "network" | "response",
    public readonly detail: {
      code?: string;
      status?: number;
      retryable?: boolean;
      fieldDetails?: Record<string, string>;
      runId?: string;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  get code(): string | undefined {
    return this.detail.code;
  }

  get status(): number | undefined {
    return this.detail.status;
  }

  get fieldDetails(): Record<string, string> {
    return this.detail.fieldDetails ?? {};
  }

  /** Present when the API named an existing run, such as an order already supervised. */
  get runId(): string | undefined {
    return this.detail.runId;
  }
}

const REQUEST_TIMEOUT_MS = 15_000;

function origin(): string {
  let resolved: string | null;
  try {
    resolved = parseApiOrigin(process.env.NEXT_PUBLIC_API_BASE_URL);
  } catch {
    throw new ApiError(
      "NEXT_PUBLIC_API_BASE_URL must be one HTTP(S) origin without credentials or a path.",
      "configuration",
    );
  }
  if (!resolved) {
    throw new ApiError(
      "The API address is missing. Set NEXT_PUBLIC_API_BASE_URL in the frontend environment and restart the console.",
      "configuration",
    );
  }
  return resolved;
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function describe(status: number, body: unknown): ApiError {
  if (body && typeof body === "object") {
    const error = body as Record<string, unknown>;
    if (typeof error.message === "string" && typeof error.code === "string") {
      return new ApiError(error.message, "response", {
        code: error.code,
        status,
        retryable: error.retryable === true,
        fieldDetails:
          error.field_details && typeof error.field_details === "object"
            ? (error.field_details as Record<string, string>)
            : undefined,
        runId: typeof error.run_id === "string" ? error.run_id : undefined,
      });
    }
  }
  if (status === 404) {
    return new ApiError("That record does not exist.", "response", { status });
  }
  return new ApiError(
    `The API returned an unexpected response (${status}).`,
    "response",
    { status },
  );
}

async function request<T>(
  path: string,
  options: {
    method?: "GET" | "POST" | "PATCH";
    body?: unknown;
    signal?: AbortSignal;
    /** A key the response must contain, so a mismatched build fails loudly. */
    expect: string;
  },
): Promise<T> {
  const base = origin();
  const signals = [AbortSignal.timeout(REQUEST_TIMEOUT_MS)];
  if (options.signal) signals.unshift(options.signal);

  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      method: options.method ?? "GET",
      cache: "no-store",
      signal: AbortSignal.any(signals),
      headers: options.body
        ? { Accept: "application/json", "Content-Type": "application/json" }
        : { Accept: "application/json" },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(
      "The API could not be reached. Check that the local service is running and allows this console, then try again.",
      "network",
    );
  }

  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw describe(response.status, data);
  if (!data || typeof data !== "object" || !(options.expect in data)) {
    throw new ApiError(
      "The API returned an unreadable response. Verify that the console and API versions match.",
      "response",
      { status: response.status },
    );
  }
  return data as T;
}

// ------------------------------------------------------------------ setup

export async function getReadiness(signal: AbortSignal): Promise<Readiness> {
  const base = origin();
  let response: Response;
  try {
    response = await fetch(`${base}/readyz`, {
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
      { status: response.status },
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

// ------------------------------------------------------ supervisor configuration

export function listSupervisors(signal?: AbortSignal): Promise<SupervisorList> {
  return request<SupervisorList>("/api/supervisors", {
    signal,
    expect: "supervisors",
  });
}

export function createSupervisor(
  draft: SupervisorDraft,
  signal?: AbortSignal,
): Promise<SupervisorRecord> {
  return request<SupervisorRecord>("/api/supervisors", {
    method: "POST",
    body: draft,
    signal,
    expect: "config",
  });
}

export function updateSupervisor(
  supervisorId: string,
  update: SupervisorUpdate,
  signal?: AbortSignal,
): Promise<SupervisorRecord> {
  return request<SupervisorRecord>(`/api/supervisors/${supervisorId}`, {
    method: "PATCH",
    body: update,
    signal,
    expect: "config",
  });
}

// --------------------------------------------------------------------- runs

export function listRuns(
  options: {
    state?: "active" | "closed" | "all";
    orderId?: string;
    cursor?: string | null;
    limit?: number;
  } = {},
  signal?: AbortSignal,
): Promise<RunPage> {
  const path = `/api/runs${query({
    state: options.state,
    order_id: options.orderId,
    cursor: options.cursor,
    limit: options.limit,
  })}`;
  return request<RunPage>(path, { signal, expect: "runs" });
}

export function getRun(runId: string, signal?: AbortSignal): Promise<RunView> {
  return request<RunView>(`/api/runs/${runId}`, { signal, expect: "snapshot" });
}

export function getActivity(
  runId: string,
  options: {
    afterSequence?: number;
    beforeSequence?: number;
    throughSequence?: number;
    category?: ActivityCategory;
    limit?: number;
  } = {},
  signal?: AbortSignal,
): Promise<ActivityPage> {
  const path = `/api/runs/${runId}/activity${query({
    after_sequence: options.afterSequence,
    before_sequence: options.beforeSequence,
    through_sequence: options.throughSequence,
    category: options.category,
    limit: options.limit,
  })}`;
  return request<ActivityPage>(path, { signal, expect: "records" });
}

/**
 * The reservation settles even when the workflow start does not. A `retry_required`
 * result is not a failure to retry blindly: send the *same* command_id again.
 */
export function createRun(
  body: CreateRunRequest,
  signal?: AbortSignal,
): Promise<RunCreated> {
  return request<RunCreated>("/api/runs", {
    method: "POST",
    body,
    signal,
    expect: "run_id",
  });
}

// ----------------------------------------------------------------- commands

function command(
  runId: string,
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<CommandAcknowledgement> {
  return request<CommandAcknowledgement>(`/api/runs/${runId}${path}`, {
    method: "POST",
    body,
    signal,
    expect: "acceptance",
  });
}

export function submitEvent(
  runId: string,
  body: EventCommand,
  signal?: AbortSignal,
): Promise<CommandAcknowledgement> {
  return command(runId, "/events", body, signal);
}

export function submitInstruction(
  runId: string,
  body: InstructionCommand,
  signal?: AbortSignal,
): Promise<CommandAcknowledgement> {
  return command(runId, "/instructions", body, signal);
}

export function pauseRun(
  runId: string,
  body: ControlCommand,
  signal?: AbortSignal,
): Promise<CommandAcknowledgement> {
  return command(runId, "/pause", body, signal);
}

export function resumeRun(
  runId: string,
  body: ControlCommand,
  signal?: AbortSignal,
): Promise<CommandAcknowledgement> {
  return command(runId, "/resume", body, signal);
}

export function terminateRun(
  runId: string,
  body: ControlCommand,
  signal?: AbortSignal,
): Promise<CommandAcknowledgement> {
  return command(runId, "/terminate", body, signal);
}

/** Approval names one exact draft; the path carries the draft's own slashed identity. */
export function reviewDraft(
  runId: string,
  body: ReviewCommand,
  signal?: AbortSignal,
): Promise<CommandAcknowledgement> {
  return command(runId, `/reviews/${body.draft_id}`, body, signal);
}
