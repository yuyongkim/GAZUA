// Sector-indicators API types

export interface CellSummary {
  latest: number | null;
  latest_date: string | null;
  rows: number;
  yoy_pct: number | null;
  z3y: number | null;
  sparkline: number[];
}

export interface MatrixCell {
  cell_id: string;          // "26_01"
  sector_num: string;       // "26"
  sector_name: string;      // "반도체"
  idx: number;              // 1
  indicator: string;
  source_raw: string;
  category: string;
  leadlag: string;
  filled: boolean;
  folder: string | null;
  slug: string | null;
  rows: number;
  summary: CellSummary;
}

export interface MatrixResponse {
  cells: MatrixCell[];
  total_cells: number;
  filled_cells: number;
  fill_pct: number;
  sector_count: number;
  indicator_per_sector: number;
  as_of: string;
}

export interface SeriesPoint {
  date: string;
  value: number;
}

export interface SeriesResponse {
  cell: {
    cell_id: string;
    sector_num: string;
    sector_name: string;
    idx: number;
    indicator: string;
    source_raw: string;
    category: string;
    leadlag: string;
  };
  series: SeriesPoint[];
  period: string;
  folder: string | null;
  slug: string | null;
  n: number;
}

export interface SectorTicker {
  ticker: string;
  name: string;
  market: string;
  sector: string;
  industry: string | null;
}

export interface SymbolsResponse {
  sector: {
    num: string;
    slug: string;
    label: string;
    universe_match: string[];
    primary_etf: string | null;
  };
  tickers: SectorTicker[];
  count: number;
}

export interface SectorMeta {
  sector_num: string;
  slug: string;
  label: string;
  universe_sectors: string[];
  primary_etf: string | null;
}

export interface SectorsResponse {
  sectors: SectorMeta[];
  count: number;
}

export interface StrengthSignal {
  indicator: string;
  z: number;
  leadlag: string;
  cell_id: string;
}

export interface SectorStrengthRow {
  rank: number;
  sector_num: string;
  sector_name: string;
  score: number | null;
  n_filled: number;
  n_total: number;
  fill_pct: number;
  top_signals: StrengthSignal[];
  bottom_signals: StrengthSignal[];
}

export interface StrengthResponse {
  rows: SectorStrengthRow[];
  count: number;
  method: string;
  as_of: string;
}

export interface LeaderStockRow {
  rank: number;
  ticker: string;
  name: string;
  market: string;
  universe_sector: string;
  wics_sector_num: string;
  wics_sector_name: string;
  leader_score: number;
  macro_score: number;
  macro_z: number | null;
  price_rs: number;
  fund_signal: number;
  reason: string;
}

export interface LeadersResponse {
  rows: LeaderStockRow[];
  count: number;
  method: string;
  top_n: number;
  as_of: string;
}

export interface BacktestResult {
  strategy: string;
  period_start: string;
  period_end: string;
  n_periods: number;
  final_nav: number;
  cagr: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
  n_trades: number;
  rebalance_freq: string;
  top_n: number;
  monthly_returns: { month: string; return: number; nav: number }[];
  benchmark_nav: number | null;
  benchmark_cagr: number | null;
}
