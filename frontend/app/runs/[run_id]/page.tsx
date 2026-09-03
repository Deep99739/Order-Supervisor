import type { Metadata } from "next";
import { RunDetail } from "@/components/runs/run-detail";

export const metadata: Metadata = { title: "Run" };

export default async function RunPage({
  params,
}: {
  params: Promise<{ run_id: string }>;
}) {
  const { run_id } = await params;
  return <RunDetail runId={run_id} />;
}
