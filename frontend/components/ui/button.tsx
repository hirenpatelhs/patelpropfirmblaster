import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Button({ className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={cn("inline-flex h-9 items-center justify-center gap-2 rounded-lg border px-3 text-xs font-semibold transition hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-50", className)} {...props} />;
}
