"use client";

import { Activity, BarChart3, Bell, BookOpen, Building2, CircleDollarSign, Gauge, History, LayoutDashboard, ListChecks, Radio, ScrollText, Settings, ShieldCheck, SlidersHorizontal, UserRoundCog, WalletCards, X } from "lucide-react";
import { cn } from "@/lib/utils";

const items = [
  ["Overview", LayoutDashboard], ["Accounts", WalletCards], ["Prop Firms", Building2], ["Signals", Radio],
  ["Live Trades", CircleDollarSign], ["Positions", ListChecks], ["Trade History", History], ["Telegram Sources", Bell],
  ["Rule Engine", BookOpen], ["Risk Manager", ShieldCheck], ["Analytics", BarChart3], ["Notifications", Activity],
  ["System Health", Gauge], ["Audit Logs", ScrollText], ["Settings", Settings],
] as const;

export function Sidebar({ active, onSelect, open, onClose }: { active: string; onSelect: (value: string) => void; open: boolean; onClose: () => void }) {
  return (
    <aside className={cn("fixed inset-y-0 left-0 z-40 flex w-[244px] flex-col border-r bg-[#090d12] transition-transform lg:translate-x-0", open ? "translate-x-0" : "-translate-x-full")}>
      <div className="flex h-[76px] items-center gap-3 border-b px-5">
        <div className="grid size-9 place-items-center rounded-lg border border-emerald-400/30 bg-emerald-400/10 font-mono text-sm font-black text-emerald-300">PPB</div>
        <div><div className="text-[13px] font-extrabold tracking-wide">PATEL PROPFIRM</div><div className="text-[10px] font-bold tracking-[.24em] text-emerald-400">BLASTER</div></div>
        <button onClick={onClose} aria-label="Close navigation" className="ml-auto lg:hidden"><X className="size-5" /></button>
      </div>
      <div className="border-b px-5 py-4"><div className="flex items-center gap-2 text-xs"><span className="pulse-dot size-2 rounded-full bg-emerald-400"/><span className="font-semibold">System operational</span></div><p className="mt-1 pl-4 text-[10px] text-[#6f7c89]">All safeguards armed</p></div>
      <nav className="flex-1 overflow-y-auto px-3 py-3" aria-label="Dashboard navigation">
        {items.map(([label, Icon]) => <button key={label} onClick={() => { onSelect(label); onClose(); }} className={cn("mb-0.5 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-[12px] font-medium text-[#8b98a6] transition hover:bg-white/[.035] hover:text-white", active === label && "bg-emerald-400/[.09] text-emerald-300 shadow-[inset_2px_0_0_#2de3a1]")}><Icon className="size-[15px]" />{label}</button>)}
      </nav>
      <div className="border-t p-3"><div className="flex items-center gap-3 rounded-lg bg-white/[.025] p-3"><div className="grid size-8 place-items-center rounded-md bg-[#19222c]"><UserRoundCog className="size-4" /></div><div><p className="text-xs font-semibold">Administrator</p><p className="text-[10px] text-[#6f7c89]">Protected session</p></div><SlidersHorizontal className="ml-auto size-4 text-[#6f7c89]" /></div></div>
    </aside>
  );
}
