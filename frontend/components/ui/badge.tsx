import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("inline-flex items-center rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-[.12em]", className)} {...props} />;
}
