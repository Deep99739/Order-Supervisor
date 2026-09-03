import * as React from "react";

import { cn } from "@/lib/utils";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn("field h-auto min-h-20 py-2.5 leading-6", className)}
      {...props}
    />
  );
}

export { Textarea };
