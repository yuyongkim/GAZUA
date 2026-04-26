// BacktestPanel — 섹터 로테이션 백테스트 결과 요약.
"use client";

import { useEffect, useState } from "react";

import { apiBase } from "@/lib/apiBase";
import type { BacktestResult } from "@/types/sector_indicators";

interface Strategy {
  label: string;
  query: string;
}
const STRATEGIES: Strategy[] = [
  { label: "TOP3 long-only", query: "top_n=3&short_bottom=false" },
  { label: "TOP3-BOT3 L/S", query: "top_n=3&short_bottom=true" },
  { label: "TOP5 long-only", query: "top_n=5&short_bottom=false" },
];

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
}

function NavSparkline({ rows }: { rows: { month: string; nav: number }[] }) {
  if (rows.length === 0) return null;
  const W = 240;
  const H = 50;
  const PAD = 4;
  const navs = rows.map((r) => r.nav);
  const min = Math.min(...navs);
  const max = Math.max(...navs);
  const range = max - min || 1;
  const xStep = (W - 2 * PAD) / Math.max(1, rows.length - 1);
  const points = rows.map((r, i) => {
    const x = PAD + i * xStep;
    const y = H - PAD - ((r.nav - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{ background: "var(--surface-2)" }}>
      <path d={`M ${points.join(" L ")}`} fill="none" stroke="#3a8a4a" strokeWidth={1.2} />
    </svg>
  );
}

export function BacktestPanel() {
  const [results, setResults] = useState<Record<string, BacktestResult | null>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all(
      STRATEGIES.map((s) =>
        fetch(`${apiBase()}/api/v1/research/sector-indicators/backtest?${s.query}`)
          .then((r) => r.json())
          .then((b) => ({ label: s.label, data: b.ok ? (b.data as BacktestResult) : null })),
      ),
    )
      .then((arr) => {
        const m: Record<string, BacktestResult | null> = {};
        arr.forEach((x) => (m[x.label] = x.data));
        setResults(m);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div
      className="rounded mb-4"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div
        className="flex items-baseline justify-between px-3 py-2 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <h2 className="display text-base">섹터 로테이션 백테스트 (월별 리밸런싱)</h2>
        <span className="text-xs opacity-60">{loading ? "계산 중…" : "거래비용 반영"}</span>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            <th className="text-left px-3 py-1">전략</th>
            <th className="text-left px-2 py-1">기간</th>
            <th className="text-right px-2 py-1">CAGR</th>
            <th className="text-right px-2 py-1">Sharpe</th>
            <th className="text-right px-2 py-1">MDD</th>
            <th className="text-right px-2 py-1">Win%</th>
            <th className="text-right px-2 py-1">vs 벤치 (EW)</th>
            <th className="text-left px-2 py-1">NAV (24M)</th>
          </tr>
        </thead>
        <tbody>
          {STRATEGIES.map((s) => {
            const r = results[s.label];
            return (
              <tr key={s.label} style={{ borderBottom: "1px solid var(--border)" }}>
                <td className="px-3 py-2 font-medium">{s.label}</td>
                <td className="px-2 py-2 opacity-70 text-[10px]">
                  {r ? `${r.period_start} ~ ${r.period_end}` : "—"}
                </td>
                <td className="px-2 py-2 text-right tabular-nums font-semibold">
                  {r ? fmtPct(r.cagr) : "—"}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {r ? r.sharpe.toFixed(2) : "—"}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {r ? fmtPct(r.max_drawdown) : "—"}
                </td>
                <td className="px-2 py-2 text-right tabular-nums opacity-70">
                  {r ? (r.win_rate * 100).toFixed(0) + "%" : "—"}
                </td>
                <td className="px-2 py-2 text-right tabular-nums opacity-70">
                  {r && r.benchmark_cagr !== null ? (
                    <>
                      {fmtPct(r.cagr - r.benchmark_cagr)} <span className="opacity-50 text-[9px]">(α)</span>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-2 py-1">
                  {r ? <NavSparkline rows={r.monthly_returns} /> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
