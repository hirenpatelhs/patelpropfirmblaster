import type { DashboardData, JsonRow } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

async function request<T>(path: string, token?: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof payload?.detail === "string" ? payload.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function login(loginValue: string, password: string): Promise<string> {
  const result = await request<{ access_token: string }>("/auth/login", undefined, { method: "POST", body: JSON.stringify({ login: loginValue, password }) });
  return result.access_token;
}

export async function loadDashboard(token: string): Promise<DashboardData> {
  const paths = ["/system/overview", "/analytics/overview", "/system/health", "/accounts", "/signals", "/positions", "/orders", "/trades", "/telegram-sources", "/prop-firms", "/rule-profiles", "/audit", "/settings"] as const;
  const [overview, analytics, health, accounts, signals, positions, orders, trades, sources, firms, rules, audit, settings] = await Promise.all(paths.map(path => request<never>(path, token)));
  const signalRows = signals as JsonRow[];
  const decisions = signalRows[0]?.id ? await request<JsonRow[]>(`/signals/${signalRows[0].id}/decisions`, token) : [];
  return { overview, analytics, health, accounts, signals: signalRows, positions, orders, trades, sources, firms, rules, audit, settings, decisions } as DashboardData;
}

export function emergencyStop(token: string): Promise<{ status: string }> {
  return request("/system/emergency-stop", token, { method: "POST", body: JSON.stringify({ stop_new_trades: true, close_all_positions: false }) });
}

export function patchAccountSettings(token: string, accountId: string, payload: Record<string, unknown>): Promise<{ status: string }> {
  return request(`/settings/${accountId}`, token, { method: "PATCH", body: JSON.stringify(payload) });
}
