"use client";

import * as React from "react";
import { Label as LabelPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn(
        "flex items-center gap-2 text-[13px] leading-5 font-medium select-none",
        "group-data-[disabled=true]:opacity-60 peer-disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}

export { Label };
