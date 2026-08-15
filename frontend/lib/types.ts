export type JsonRow = Record<string, unknown>;

export interface Account {
  id: string; name: string; platform: string; balance: number; equity: number;
  stage: string; status: string; trading_mode: string; risk_mode: string;
}

export interface OverviewData {
  accounts: { total: number; active: number; evaluation: number; funded: number; paused: number };
  financials: { starting_balance: number; balance: number; equity: number; floating_pnl: number };
  signals_today: number;
}

export interface HealthData {
  status: string;
  timestamp: string;
  checks: Record<string, string>;
}

export interface AnalyticsData {
  accounts: number; total_balance: number; total_equity: number; signals: number;
  approved_decisions: number; trades: number; realized_pnl: number; wins: number; open_positions: number;
}

export interface DashboardData {
  overview: OverviewData;
  analytics: AnalyticsData;
  health: HealthData;
  accounts: Account[];
  signals: JsonRow[];
  positions: JsonRow[];
  orders: JsonRow[];
  trades: JsonRow[];
  sources: JsonRow[];
  firms: JsonRow[];
  rules: JsonRow[];
  audit: JsonRow[];
  settings: JsonRow[];
  decisions: JsonRow[];
}
