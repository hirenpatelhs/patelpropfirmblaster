"use client";

import { FormEvent, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { patchAccountSettings } from "@/lib/api";
import type { DashboardData, JsonRow } from "@/lib/types";

const modules: Record<string, keyof DashboardData> = {
  Accounts: "accounts", "Prop Firms": "firms", Signals: "signals", "Live Trades": "orders",
  Positions: "positions", "Trade History": "trades", "Telegram Sources": "sources",
  "Rule Engine": "rules", Analytics: "audit", "Audit Logs": "audit",
};

export function ModuleView({ name, data, token, onChanged }: { name: string; data: DashboardData; token: string; onChanged: () => Promise<void> }) {
  if (name === "System Health") return <Health data={data}/>;
  if (name === "Risk Manager") return <Summary title="Risk manager" rows={[data.analytics as unknown as JsonRow]} />;
  if (name === "Notifications") return <Empty title={name} message="Notifications are delivered from the durable backend queue; no queued notification API rows are available."/>;
  if (name === "Settings") return <SettingsEditor data={data} token={token} onChanged={onChanged}/>;
  const key = modules[name];
  const rows = key ? data[key] : [];
  if (!Array.isArray(rows)) return <Summary title={name} rows={[rows as unknown as JsonRow]}/>;
  return <Summary title={name} rows={rows as JsonRow[]} />;
}

function Summary({ title, rows }: { title: string; rows: JsonRow[] }) {
  const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row)))).filter(key => !["raw_text", "request", "response", "before", "after", "metadata"].includes(key)).slice(0, 8);
  return <Card className="overflow-hidden"><div className="border-b p-5"><p className="text-sm font-semibold">{title}</p><p className="mt-1 text-[10px] text-[#71808e]">Authenticated API data · refreshed on demand</p></div>{rows.length === 0 ? <p className="p-10 text-center text-xs text-[#71808e]">No records yet.</p> : <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left"><thead><tr className="border-b text-[9px] uppercase tracking-wider text-[#63717e]">{columns.map(column => <th className="px-4 py-3" key={column}>{column.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{rows.map((row,index) => <tr key={String(row.id ?? index)} className="border-b text-[11px] last:border-0">{columns.map(column => <td className="max-w-56 truncate px-4 py-3" title={display(row[column])} key={column}>{display(row[column])}</td>)}</tr>)}</tbody></table></div>}</Card>;
}

function Health({data}:{data:DashboardData}) { return <Card className="p-5"><p className="text-sm font-semibold">System health: {data.health.status}</p><div className="mt-5 grid gap-3 md:grid-cols-3">{Object.entries(data.health.checks).map(([name,status]) => <div key={name} className="rounded-lg border p-4"><p className="text-[10px] uppercase text-[#71808e]">{name}</p><p className="mt-2 text-sm font-semibold">{status}</p></div>)}</div></Card>; }
function Empty({title,message}:{title:string;message:string}) { return <Card className="p-8"><p className="font-semibold">{title}</p><p className="mt-2 text-xs text-[#71808e]">{message}</p></Card>; }
function display(value: unknown): string { if (value == null) return "—"; if (typeof value === "object") return JSON.stringify(value); return String(value); }

function SettingsEditor({data,token,onChanged}:{data:DashboardData;token:string;onChanged:()=>Promise<void>}) { const [accountId,setAccountId]=useState(data.accounts[0]?.id??""); const [canonical,setCanonical]=useState("XAUUSD"); const [mapped,setMapped]=useState(""); const [message,setMessage]=useState(""); async function submit(event:FormEvent){event.preventDefault();const current=data.settings.find(row=>row.account_id===accountId);const mappings=(current?.symbol_mappings??{}) as Record<string,string>;try{await patchAccountSettings(token,accountId,{symbol_mappings:{...mappings,[canonical.trim().toUpperCase()]:mapped.trim()}});setMessage("Mapping saved and audited.");await onChanged()}catch(reason){setMessage(reason instanceof Error?reason.message:"Update failed")}} if(!data.accounts.length)return <Empty title="Settings" message="Create an account before configuring broker symbol mappings."/>; return <Card className="max-w-2xl p-6"><p className="font-semibold">Broker symbol mapping</p><p className="mt-1 text-xs text-[#71808e]">Canonical signal symbols remain unchanged; mapping applies only during account execution.</p><form onSubmit={submit} className="mt-6 grid gap-4 md:grid-cols-2"><label className="text-xs md:col-span-2">Account<select value={accountId} onChange={event=>setAccountId(event.target.value)} className="mt-2 w-full rounded-lg border bg-[#090e13] p-2.5">{data.accounts.map(account=><option key={account.id} value={account.id}>{account.name}</option>)}</select></label><label className="text-xs">Canonical symbol<input required value={canonical} onChange={event=>setCanonical(event.target.value)} className="mt-2 w-full rounded-lg border bg-[#090e13] p-2.5"/></label><label className="text-xs">Broker symbol<input required placeholder="XAUUSD.a" value={mapped} onChange={event=>setMapped(event.target.value)} className="mt-2 w-full rounded-lg border bg-[#090e13] p-2.5"/></label><Button className="md:col-span-2">Save mapping</Button>{message&&<p role="status" className="text-xs text-emerald-300 md:col-span-2">{message}</p>}</form></Card>; }
