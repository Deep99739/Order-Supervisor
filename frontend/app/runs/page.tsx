import type { Metadata } from "next";
import { RunList } from "@/components/runs/run-list";
import { ServiceCheck } from "@/components/service-check";

export const metadata: Metadata = { title: "Runs" };

export default function RunsPage() {
  return (
    <div className="space-y-7">
      <RunList />
      <ServiceCheck />
    </div>
  );
}
