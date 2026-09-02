import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ServiceCheck } from "@/components/service-check";

export const metadata: Metadata = { title: "Runs" };

export default function RunsPage() {
  return (
    <div className="space-y-7">
      <div>
        <h1 className="text-[28px] font-semibold tracking-tight">Runs</h1>
        <p className="mt-2 text-muted-foreground">
          Follow each order from creation to completion.
        </p>
      </div>
      <Card className="gap-0 overflow-hidden py-0 shadow-none">
        <div className="border-b px-6 py-4">
          <h2 className="font-medium">Order supervision</h2>
        </div>
        <CardContent className="flex min-h-[342px] flex-col items-center justify-center px-6 py-12 text-center">
          <div className="mb-5 flex size-14 items-center justify-center rounded-2xl border border-primary/15 bg-accent text-primary">
            <Inbox className="size-6" strokeWidth={1.6} aria-hidden="true" />
          </div>
          <h3 className="text-lg font-semibold tracking-tight">
            Your run workspace is taking shape
          </h3>
          <p className="mt-2 max-w-[420px] leading-6 text-muted-foreground">
            Run creation and history are not available in this build. No order
            data is loaded here yet.
          </p>
          <Button asChild variant="outline" className="mt-6 h-11">
            <Link href="/supervisors">
              Explore supervisor setup
              <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          </Button>
        </CardContent>
        <div className="border-t bg-muted/35 px-6 py-4 text-center text-[13px] leading-5 text-muted-foreground">
          When connected, each run will bring its order facts, activity, and
          next review together.
        </div>
      </Card>
      <ServiceCheck />
    </div>
  );
}
