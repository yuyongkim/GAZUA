// LeaderStocksPanel — 종목 단위 leader score TOP-N 표시.
"use client";

import { useEffect, useState } from "react";

import { apiBase } from "@/lib/apiBase";
import type { LeadersResponse, LeaderStockRow } from "@/types/sector_indicators";

export function LeaderStocksPanel() {
  const [data, setData] = useState<LeadersResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`${apiBase()}/api/v1/research/sector-indicators/leaders?top_n=20`)
      .then((r) => r.json())
      .then((b) => {
        if (b.ok && b.data) setData(b.data as LeadersResponse);
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
        <h2 className="display text-base">주도주 후보 TOP 20 (Macro × 가격 RS)</h2>
        <span className="text-xs opacity-60">
          {loading ? "로딩…" : data?.as_of}
        </span>
      </div>
      {data && data.rows.length > 0 ? (
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th className="text-left px-3 py-1">#</th>
              <th className="text-left px-2 py-1">티커</th>
              <th className="text-left px-2 py-1">종목명</th>
              <th className="text-left px-2 py-1">섹터</th>
              <th className="text-right px-2 py-1">Leader</th>
              <th className="text-right px-2 py-1">Macro z</th>
              <th className="text-left px-2 py-1">사유</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.ticker} style={{ borderBottom: "1px solid var(--border)" }}>
                <td className="px-3 py-1 opacity-60">#{r.rank}</td>
                <td className="px-2 py-1 font-mono">{r.ticker}</td>
                <td className="px-2 py-1">{r.name}</td>
                <td className="px-2 py-1 opacity-70">{r.wics_sector_name}</td>
                <td className="px-2 py-1 text-right tabular-nums font-semibold">
                  {(r.leader_score * 100).toFixed(1)}
                </td>
                <td className="px-2 py-1 text-right tabular-nums opacity-70">
                  {r.macro_z !== null ? (r.macro_z >= 0 ? "+" : "") + r.macro_z.toFixed(2) : "—"}
                </td>
                <td className="px-2 py-1 text-[10px] opacity-70">{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !loading && <div className="text-xs opacity-60 py-4 text-center">데이터 없음</div>
      )}
    </div>
  );
}
