import type { Metadata } from "next";
import { SupervisorWorkbench } from "@/components/supervisors/supervisor-workbench";

export const metadata: Metadata = { title: "Supervisors" };

export default function SupervisorsPage() {
  return <SupervisorWorkbench />;
}
