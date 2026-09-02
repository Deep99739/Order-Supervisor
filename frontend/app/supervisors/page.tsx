import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowLeft,
  FileText,
  ListChecks,
  SlidersHorizontal,
  Timer,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const metadata: Metadata = { title: "Supervisors" };

const settings = [
  {
    icon: FileText,
    title: "Instructions",
    description: "The context and priorities that guide an order’s supervisor.",
  },
  {
    icon: ListChecks,
    title: "Allowed actions",
    description:
      "Which teams it may message, and when customer contact needs review.",
  },
  {
    icon: Timer,
    title: "Review timing",
    description: "When to check an order again and how long to supervise it.",
  },
];

export default function SupervisorsPage() {
  return (
    <div className="space-y-7">
      <div>
        <h1 className="text-[28px] font-semibold tracking-tight">
          Supervisors
        </h1>
        <p className="mt-2 max-w-xl leading-6 text-muted-foreground">
          Set the instructions, actions, and review behaviour for an order.
        </p>
      </div>
      <Card className="gap-0 overflow-hidden py-0 shadow-none">
        <CardContent className="flex min-h-[325px] flex-col items-center justify-center px-6 py-12 text-center">
          <div className="mb-5 flex size-14 items-center justify-center rounded-2xl border border-primary/15 bg-accent text-primary">
            <SlidersHorizontal
              className="size-6"
              strokeWidth={1.6}
              aria-hidden="true"
            />
          </div>
          <h2 className="text-lg font-semibold tracking-tight">
            Supervisor setup is coming next
          </h2>
          <p className="mt-2 max-w-[430px] leading-6 text-muted-foreground">
            Creating and saving configurations is not available in this build.
            This is where you will choose how orders are supervised.
          </p>
          <Button asChild variant="outline" className="mt-6 h-11">
            <Link href="/runs">
              <ArrowLeft className="size-4" aria-hidden="true" />
              Back to runs
            </Link>
          </Button>
        </CardContent>
        <div className="grid border-t bg-muted/35 md:grid-cols-3">
          {settings.map(({ icon: Icon, title, description }) => (
            <div
              key={title}
              className="border-b p-6 last:border-b-0 md:border-r md:border-b-0 md:last:border-r-0"
            >
              <Icon
                className="mb-3 size-[18px] text-primary"
                aria-hidden="true"
              />
              <h3 className="font-medium">{title}</h3>
              <p className="mt-2 text-[13px] leading-5 text-muted-foreground">
                {description}
              </p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
