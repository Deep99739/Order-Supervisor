import type { Metadata } from "next";
import { PackageCheck } from "lucide-react";
import { Navigation } from "@/components/navigation";
import { Notifications } from "@/components/ui/notification";
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
        <Notifications>
          <div className="mx-auto min-h-dvh max-w-[1660px] md:grid md:grid-cols-[216px_minmax(0,1fr)]">
            <aside className="sticky top-0 z-30 border-b bg-card/95 backdrop-blur md:flex md:h-dvh md:flex-col md:border-r md:border-b-0 md:px-4 md:py-7 md:backdrop-blur-none">
              <div className="flex items-center gap-2.5 px-5 py-3.5 md:px-2 md:py-0">
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
              <div className="border-t px-3 py-2 md:mt-10 md:border-t-0 md:px-0 md:py-0">
                <Navigation />
              </div>
              <div className="mt-auto hidden px-3 pt-8 md:block">
                <p className="text-[13px] font-medium">Local workspace</p>
                <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
                  Every recorded action here
                  <br />
                  is a simulation.
                </p>
              </div>
            </aside>
            <div className="min-w-0">
              <main
                id="main-content"
                tabIndex={-1}
                className="mx-auto max-w-[1440px] px-5 py-7 outline-none sm:px-8 lg:px-10 lg:py-9"
              >
                {children}
              </main>
            </div>
          </div>
        </Notifications>
      </body>
    </html>
  );
}
