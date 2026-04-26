// CellDrawer — 셀 클릭 시 우측 drawer로 시계열 + 해당 sector 종목 리스트 표시.
"use client";

import { useEffect, useState } from "react";

import { apiBase } from "@/lib/apiBase";
import type {
  MatrixCell,
  SeriesResponse,
  SymbolsResponse,
} from "@/types/sector_indicators";

interface Props {
  cell: MatrixCell | null;
  onClose: () => void;
}

const PERIODS = ["1y", "3y", "10y", "all"] as const;
type Period = (typeof PERIODS)[number];

function fmtNum(v: number | null): string {
  if (v === null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

// Inline SVG sparkline
function MiniChart({ data }: { data: { date: string; value: number }[] }) {
  if (data.length === 0) {
    return <div className="text-xs opacity-50 py-12 text-center">데이터 없음</div>;
  }
  const W = 600;
  const H = 220;
  const PAD = 30;
  const values = data.map((d) => d.value).filter((v) => Number.isFinite(v));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const xStep = (W - 2 * PAD) / Math.max(1, data.length - 1);
  const points = data.map((d, i) => {
    const x = PAD + i * xStep;
    const y = H - PAD - ((d.value - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const pathD = `M ${points.join(" L ")}`;
  const firstDate = data[0].date;
  const lastDate = data[data.length - 1].date;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ background: "var(--surface-2)" }}>
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--border)" />
      <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--border)" />
      <text x={PAD} y={H - 8} fontSize={10} fill="var(--fg-muted)">{firstDate}</text>
      <text x={W - PAD} y={H - 8} fontSize={10} fill="var(--fg-muted)" textAnchor="end">{lastDate}</text>
      <text x={PAD - 4} y={PAD} fontSize={10} fill="var(--fg-muted)" textAnchor="end">{fmtNum(max)}</text>
      <text x={PAD - 4} y={H - PAD} fontSize={10} fill="var(--fg-muted)" textAnchor="end">{fmtNum(min)}</text>
      <path d={pathD} fill="none" stroke="#3a8a4a" strokeWidth={1.5} />
    </svg>
  );
}

export function CellDrawer({ cell, onClose }: Props) {
  const [series, setSeries] = useState<SeriesResponse | null>(null);
  const [symbols, setSymbols] = useState<SymbolsResponse | null>(null);
  const [period, setPeriod] = useState<Period>("all");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!cell) return;
    setLoading(true);
    Promise.all([
      fetch(`${apiBase()}/api/v1/research/sector-indicators/series/${cell.cell_id}?period=${period}`)
        .then((r) => r.json()).then((b) => (b.ok ? b.data : null)),
      fetch(`${apiBase()}/api/v1/research/sector-indicators/symbols?sector=${cell.sector_num}&limit=30`)
        .then((r) => r.json()).then((b) => (b.ok ? b.data : null)),
    ])
      .then(([s, sym]) => {
        setSeries(s as SeriesResponse | null);
        setSymbols(sym as SymbolsResponse | null);
      })
      .finally(() => setLoading(false));
  }, [cell, period]);

  if (!cell) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.5)",
          zIndex: 40,
        }}
      />
      {/* Drawer */}
      <aside
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          width: "min(720px, 95vw)",
          height: "100vh",
          background: "var(--surface)",
          borderLeft: "1px solid var(--border)",
          zIndex: 50,
          padding: "16px 20px",
          overflowY: "auto",
        }}
      >
        <div className="flex items-baseline justify-between mb-3">
          <div>
            <div className="text-xs opacity-60">[{cell.sector_num}] {cell.sector_name} · #{cell.idx}</div>
            <h2 className="display text-xl mt-1">{cell.indicator}</h2>
            <div className="text-xs opacity-60 mt-1">
              {cell.source_raw} · {cell.category} · {cell.leadlag}
              {cell.slug ? ` · ${cell.folder}/${cell.slug}` : ""}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "1px solid var(--border)",
              color: "var(--fg-muted)",
              padding: "4px 10px",
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>

        {/* Stats summary */}
        <div className="grid grid-cols-4 gap-2 mb-4 text-sm">
          <div className="px-3 py-2 rounded" style={{ background: "var(--surface-2)" }}>
            <div className="text-xs opacity-60">최근값</div>
            <div className="font-semibold tabular-nums">{fmtNum(cell.summary.latest)}</div>
            <div className="text-xs opacity-60">{cell.summary.latest_date ?? "—"}</div>
          </div>
          <div className="px-3 py-2 rounded" style={{ background: "var(--surface-2)" }}>
            <div className="text-xs opacity-60">12M%</div>
            <div className="font-semibold tabular-nums">
              {cell.summary.yoy_pct === null ? "—" : (cell.summary.yoy_pct > 0 ? "+" : "") + cell.summary.yoy_pct.toFixed(1) + "%"}
            </div>
          </div>
          <div className="px-3 py-2 rounded" style={{ background: "var(--surface-2)" }}>
            <div className="text-xs opacity-60">3Y z-score</div>
            <div className="font-semibold tabular-nums">
              {cell.summary.z3y === null ? "—" : cell.summary.z3y.toFixed(2)}
            </div>
          </div>
          <div className="px-3 py-2 rounded" style={{ background: "var(--surface-2)" }}>
            <div className="text-xs opacity-60">rows</div>
            <div className="font-semibold tabular-nums">{cell.summary.rows.toLocaleString()}</div>
          </div>
        </div>

        {/* Period toggle */}
        <div className="flex gap-1 mb-2">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              style={{
                background: period === p ? "var(--accent)" : "transparent",
                color: period === p ? "var(--fg-on-accent, #000)" : "var(--fg)",
                border: "1px solid var(--border)",
                padding: "3px 12px",
                fontSize: 11,
                cursor: "pointer",
              }}
            >
              {p.toUpperCase()}
            </button>
          ))}
          {loading && <span className="text-xs opacity-60 ml-2">로딩…</span>}
        </div>

        {/* Time series chart */}
        <div className="rounded mb-4" style={{ border: "1px solid var(--border)" }}>
          <MiniChart data={series?.series ?? []} />
        </div>

        {/* Sector tickers */}
        <div className="mt-4">
          <h3 className="display text-sm mb-2">
            {cell.sector_name} 섹터 종목 ({symbols?.count ?? 0})
            {symbols?.sector?.primary_etf ? (
              <span className="ml-2 text-xs opacity-60">대표 ETF: {symbols.sector.primary_etf}</span>
            ) : null}
          </h3>
          {!symbols || symbols.tickers.length === 0 ? (
            <div className="text-xs opacity-60 py-4 text-center">매핑된 종목 없음</div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <th className="text-left py-1 px-2">티커</th>
                  <th className="text-left py-1 px-2">종목명</th>
                  <th className="text-left py-1 px-2">시장</th>
                  <th className="text-left py-1 px-2">업종</th>
                </tr>
              </thead>
              <tbody>
                {symbols.tickers.map((t) => (
                  <tr key={t.ticker} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td className="py-1 px-2 font-mono">{t.ticker}</td>
                    <td className="py-1 px-2">{t.name}</td>
                    <td className="py-1 px-2 text-xs opacity-70">{t.market}</td>
                    <td className="py-1 px-2 text-xs opacity-70">{t.industry ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </aside>
    </>
  );
}
