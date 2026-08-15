import { ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { DashboardData } from "@/lib/types";

const money = new Intl.NumberFormat("en-GB", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

function Metric({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return <Card className="p-4"><p className="text-[10px] font-bold uppercase tracking-[.12em] text-[#74818e]">{label}</p><p className="mt-2 text-[21px] font-semibold tracking-tight">{value}</p><p className="mt-1 text-[10px] text-[#687582]">{detail}</p></Card>;
}

export function Overview({ data }: { data: DashboardData }) {
  const { overview, analytics, accounts } = data;
  return <div className="space-y-5">
    <section className="grid grid-cols-2 gap-3 xl:grid-cols-5"><Metric label="Total accounts" value={overview.accounts.total} detail={money.format(overview.financials.starting_balance) + " allocated"}/><Metric label="Active" value={overview.accounts.active} detail="Execution eligible"/><Metric label="Evaluation" value={overview.accounts.evaluation} detail="Conservative mode"/><Metric label="Funded" value={overview.accounts.funded} detail="Payout protection"/><Metric label="Paused" value={overview.accounts.paused} detail="Safety intervention"/></section>
    <section className="grid gap-4 lg:grid-cols-2"><Card className="p-5"><p className="text-sm font-semibold">Portfolio capital</p><div className="mt-5 grid grid-cols-2 gap-5"><MetricValue label="Starting balance" value={money.format(overview.financials.starting_balance)}/><MetricValue label="Current balance" value={money.format(overview.financials.balance)}/><MetricValue label="Equity" value={money.format(overview.financials.equity)}/><MetricValue label="Floating P/L" value={money.format(overview.financials.floating_pnl)}/></div></Card><Card className="p-5"><div className="flex items-center justify-between"><p className="text-sm font-semibold">SHADOW execution state</p><ShieldCheck className="size-5 text-emerald-400"/></div><div className="mt-5 grid grid-cols-2 gap-5"><MetricValue label="Signals" value={String(analytics.signals)}/><MetricValue label="Approved decisions" value={String(analytics.approved_decisions)}/><MetricValue label="Open positions" value={String(analytics.open_positions)}/><MetricValue label="Realized P/L" value={money.format(analytics.realized_pnl)}/></div><p className="mt-5 rounded-lg border border-emerald-400/15 bg-emerald-400/[.05] p-3 text-[11px] text-[#a6b4bf]">All dashboard values are loaded from the authenticated API. No live broker orders are submitted in SHADOW mode.</p></Card></section>
    <Card className="overflow-hidden"><div className="flex items-center justify-between border-b px-5 py-4"><div><p className="text-sm font-semibold">Account safety grid</p><p className="mt-0.5 text-[10px] text-[#71808e]">Current database state</p></div><Badge className="border-emerald-400/20 bg-emerald-400/[.06] text-emerald-300">Risk first</Badge></div>{accounts.length === 0 ? <Empty label="No trading accounts configured."/> : <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left"><thead><tr className="border-b text-[9px] uppercase tracking-wider text-[#63717e]">{["Account","Platform","Mode","Balance","Equity","Stage","Status"].map(label => <th className="px-4 py-3" key={label}>{label}</th>)}</tr></thead><tbody>{accounts.map(account => <tr key={account.id} className="border-b text-xs last:border-0"><td className="px-4 py-3 font-semibold">{account.name}</td><td className="px-4">{account.platform}</td><td className="px-4">{account.trading_mode}</td><td className="px-4 font-mono">{money.format(account.balance)}</td><td className="px-4 font-mono">{money.format(account.equity)}</td><td className="px-4">{account.stage}</td><td className="px-4"><Badge>{account.status}</Badge></td></tr>)}</tbody></table></div>}</Card>
  </div>;
}

function MetricValue({label,value}:{label:string;value:string}) { return <div><p className="text-[10px] uppercase text-[#6f7d89]">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>; }
function Empty({label}:{label:string}) { return <p className="p-8 text-center text-xs text-[#71808e]">{label}</p>; }
