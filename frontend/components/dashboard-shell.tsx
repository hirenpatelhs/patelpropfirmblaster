"use client";

import { FormEvent, useCallback, useState } from "react";
import { Bell, Menu, Octagon, RefreshCw, Server } from "lucide-react";
import { Sidebar } from "@/components/sidebar";
import { Overview } from "@/components/overview";
import { ModuleView } from "@/components/module-view";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { emergencyStop, loadDashboard, login } from "@/lib/api";
import type { DashboardData } from "@/lib/types";
import { useUtcClock } from "@/hooks/use-utc-clock";

export function DashboardShell() {
  const [token, setToken] = useState("");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState("Overview");
  const [menu, setMenu] = useState(false);
  const [stopped, setStopped] = useState(false);
  const time = useUtcClock();
  const refresh = useCallback(async (accessToken: string) => { setLoading(true); setError(""); try { setData(await loadDashboard(accessToken)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Dashboard request failed"); } finally { setLoading(false); } }, []);
  if (!token) return <Login onToken={async accessToken => { setToken(accessToken); await refresh(accessToken); }} error={error} setError={setError}/>;
  return <div className="min-h-screen"><Sidebar active={active} onSelect={setActive} open={menu} onClose={()=>setMenu(false)}/>{menu&&<button aria-label="Close menu backdrop" onClick={()=>setMenu(false)} className="fixed inset-0 z-30 bg-black/60 lg:hidden"/>}<main className="lg:pl-[244px]"><header className="sticky top-0 z-20 flex h-[76px] items-center border-b bg-[#080c11]/95 px-4 backdrop-blur-xl md:px-6"><button className="mr-3 lg:hidden" onClick={()=>setMenu(true)} aria-label="Open navigation"><Menu className="size-5"/></button><div><h1 className="text-[15px] font-semibold">{active}</h1><p className="mt-1 hidden text-[10px] text-[#6e7b88] sm:block">Prop-Firm Signal Execution & Risk Control</p></div><div className="ml-auto flex items-center gap-2"><span className="hidden font-mono text-[10px] text-[#76838f] md:block">{time}</span><button onClick={()=>void refresh(token)} className="grid size-9 place-items-center rounded-lg border" aria-label="Refresh data"><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`}/></button><button className="grid size-9 place-items-center rounded-lg border" aria-label="Notifications"><Bell className="size-4"/></button><Button onClick={async()=>{try{await emergencyStop(token);setStopped(true);await refresh(token)}catch(reason){setError(reason instanceof Error?reason.message:"Emergency stop failed")}}} disabled={stopped}><Octagon className="size-4"/>{stopped?"TRADING STOPPED":"EMERGENCY STOP"}</Button></div></header><div className="p-4 md:p-6"><div className="mb-5 flex items-end justify-between"><div><p className="text-[10px] font-bold uppercase tracking-[.18em] text-emerald-400">Control center</p><h2 className="mt-1 text-xl font-semibold">{active === "Overview" ? "Portfolio command" : active}</h2></div><span className="hidden items-center gap-2 text-[11px] text-[#82909c] md:flex"><Server className="size-3.5"/>API-backed workspace</span></div>{error&&<p role="alert" className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/[.05] p-3 text-xs text-rose-300">{error}</p>}{data ? (active === "Overview" ? <Overview data={data}/> : <ModuleView name={active} data={data} token={token} onChanged={()=>refresh(token)}/>) : <Card className="p-10 text-center text-xs text-[#71808e]">{loading ? "Loading operational data…" : "No dashboard data available."}</Card>}</div></main></div>;
}

function Login({onToken,error,setError}:{onToken:(token:string)=>Promise<void>;error:string;setError:(value:string)=>void}) { const [loginValue,setLoginValue]=useState(""); const [password,setPassword]=useState(""); const [busy,setBusy]=useState(false); async function submit(event:FormEvent){event.preventDefault();setBusy(true);setError("");try{await onToken(await login(loginValue,password))}catch(reason){setError(reason instanceof Error?reason.message:"Login failed")}finally{setBusy(false)}} return <main className="grid min-h-screen place-items-center p-4"><Card className="w-full max-w-sm p-6"><div className="mb-6"><p className="text-[10px] font-bold uppercase tracking-[.2em] text-emerald-400">Patel Propfirm Blaster</p><h1 className="mt-2 text-xl font-semibold">Protected dashboard</h1><p className="mt-1 text-xs text-[#71808e]">Sign in with the administrator created by the API bootstrap endpoint.</p></div><form className="space-y-4" onSubmit={submit}><label className="block text-xs">Username or email<input required minLength={3} autoComplete="username" value={loginValue} onChange={event=>setLoginValue(event.target.value)} className="mt-2 w-full rounded-lg border bg-[#090e13] px-3 py-2.5 outline-none focus:border-emerald-400"/></label><label className="block text-xs">Password<input required minLength={12} type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)} className="mt-2 w-full rounded-lg border bg-[#090e13] px-3 py-2.5 outline-none focus:border-emerald-400"/></label>{error&&<p role="alert" className="text-xs text-rose-300">{error}</p>}<Button className="w-full" disabled={busy}>{busy?"Signing in…":"Sign in"}</Button></form></Card></main>; }
