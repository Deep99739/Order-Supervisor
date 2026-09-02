"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ListChecks, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";

const items = [
  { href: "/runs", label: "Runs", icon: ListChecks },
  { href: "/supervisors", label: "Supervisors", icon: SlidersHorizontal },
];

export function Navigation() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Main navigation"
      className="flex gap-2 md:flex-col md:gap-1"
    >
      {items.map(({ href, label, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex min-h-11 items-center gap-3 rounded-lg px-3 py-2.5 font-medium transition-colors duration-150",
              active
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="size-[18px]" aria-hidden="true" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
