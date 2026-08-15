import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-[#202a35] bg-[#0d1218] shadow-[0_12px_34px_rgba(0,0,0,.18)]", className)} {...props} />;
}
