import type { Metadata } from "next";
import { PackageCheck } from "lucide-react";
import { Navigation } from "@/components/navigation";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Order Supervisor", template: "%s · Order Supervisor" },
  description: "A local order operations workspace.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a
          href="#main-content"
          className="fixed top-3 left-3 z-50 -translate-y-24 rounded-lg bg-primary px-4 py-3 text-primary-foreground focus:translate-y-0"
        >
          Skip to content
        </a>
        <div className="mx-auto min-h-dvh max-w-[1660px] md:grid md:grid-cols-[216px_minmax(0,1fr)]">
          <aside className="border-b bg-card px-4 py-4 md:sticky md:top-0 md:flex md:h-dvh md:flex-col md:border-r md:border-b-0 md:px-4 md:py-7">
            <div className="flex items-center gap-2.5 px-2">
              <span className="rounded-lg bg-primary p-2 text-primary-foreground">
                <PackageCheck className="size-5" aria-hidden="true" />
              </span>
              <div>
                <p className="text-[15px] font-semibold tracking-tight">
                  Order Supervisor
                </p>
                <p className="mt-0.5 text-[13px] text-muted-foreground">
                  Order operations
                </p>
              </div>
            </div>
            <div className="mt-5 md:mt-10">
              <Navigation />
            </div>
            <div className="mt-auto hidden px-3 pt-8 md:block">
              <p className="text-[13px] font-medium">Local workspace</p>
              <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
                An order console,
                <br />
                built one step at a time.
              </p>
            </div>
          </aside>
          <div className="min-w-0">
            <header className="flex h-16 items-center justify-between border-b bg-card/75 px-5 sm:px-8 lg:px-10">
              <p className="text-muted-foreground">Workspace</p>
              <span className="rounded-md border px-2.5 py-1 text-[13px] text-muted-foreground">
                Foundation preview
              </span>
            </header>
            <main
              id="main-content"
              tabIndex={-1}
              className="mx-auto max-w-[1440px] px-5 py-8 outline-none sm:px-8 lg:px-10 lg:py-10"
            >
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
