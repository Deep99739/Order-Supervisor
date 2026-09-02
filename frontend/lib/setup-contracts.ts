// Readiness is separate from the order-domain DTOs. It never proves a working agent.
export type Readiness = {
  status: "ready" | "degraded";
  checked_at: string;
  database: "available" | "unavailable";
  temporal: "available" | "unavailable";
  worker: "not_checked";
  model: "configured_not_tested" | "missing_configuration" | "scripted";
  agent_mode: "live" | "scripted";
  demo_mode: boolean;
};

export function isReadiness(value: unknown): value is Readiness {
  if (typeof value !== "object" || value === null) return false;
  const data = value as Record<string, unknown>;
  return (
    (data.status === "ready" || data.status === "degraded") &&
    typeof data.checked_at === "string" &&
    /^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$/.test(data.checked_at) &&
    Number.isFinite(Date.parse(data.checked_at)) &&
    (data.database === "available" || data.database === "unavailable") &&
    (data.temporal === "available" || data.temporal === "unavailable") &&
    data.worker === "not_checked" &&
    ["configured_not_tested", "missing_configuration", "scripted"].includes(
      String(data.model),
    ) &&
    (data.agent_mode === "live" || data.agent_mode === "scripted") &&
    typeof data.demo_mode === "boolean"
  );
}
