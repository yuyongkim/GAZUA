// StrengthRanking — 매트릭스 페이지 상단 sector 강도 랭킹 패널.
// detrended (default) / raw 토글. TOP 5 + BOTTOM 5 + 클릭 시 sector row로 스크롤.
"use client";

import { useEffect, useState } from "react";

import { apiBase } from "@/lib/apiBase";
import type { StrengthResponse, SectorStrengthRow } from "@/types/sector_indicators";

interface Props {
  onSectorClick?: (sectorNum: string) => void;
}

type Method = "detrended" | "raw";

function bucketByScore(s: number | null): number {
  if (s === null) return 0;
  if (s <= -1) return 1;
  if (s <= -0.5) return 2;
  if (s < 0) return 3;
  if (s < 0.5) return 4;
  if (s < 1.5) return 5;
  return 6;
}

const BG = ["var(--surface-2)", "#7a1f1f", "#9a3535", "#7a4040", "#3a5a40", "#2d7a44", "#1f9a4a"];
const FG = ["var(--fg-muted)", "#fff", "#fff", "#fff", "#e8f5e9", "#fff", "#fff"];

function fmtScore(s: number | null): string {
  if (s === null || Number.isNaN(s)) return "—";
  return (s > 0 ? "+" : "") + s.toFixed(2);
}

function Row({ r, onClick }: { r: SectorStrengthRow; onClick?: () => void }) {
  const b = bucketByScore(r.score);
  return (
    <div
      onClick={onClick}
      className="cursor-pointer hover:opacity-80 transition-opacity"
      style={{
        display: "grid",
        gridTemplateColumns: "32px 1fr 60px 50px",
        alignItems: "center",
        padding: "5px 8px",
        borderBottom: "1px solid var(--border)",
        fontSize: 11,
      }}
    >
      <span className="opacity-50 font-mono">#{r.rank}</span>
      <span className="truncate">
        <span className="opacity-50 mr-1">[{r.sector_num}]</span>
        {r.sector_name}
      </span>
      <span
        className="text-right tabular-nums px-2 rounded"
        style={{ background: BG[b], color: FG[b], fontWeight: 600 }}
      >
        {fmtScore(r.score)}
      </span>
      <span className="text-right opacity-60 text-[10px]">
        {r.n_filled}/{r.n_total}
      </span>
    </div>
  );
}

export function StrengthRanking({ onSectorClick }: Props) {
  const [data, setData] = useState<StrengthResponse | null>(null);
  const [method, setMethod] = useState<Method>("detrended");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`${apiBase()}/api/v1/research/sector-indicators/strength?method=${method}`)
      .then((r) => r.json())
      .then((b) => {
        if (b.ok && b.data) setData(b.data as StrengthResponse);
      })
      .finally(() => setLoading(false));
  }, [method]);

  const rows = data?.rows ?? [];
  const top5 = rows.slice(0, 5);
  const bot5 = rows.slice(-5).reverse();

  return (
    <div
      className="rounded mb-4"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div
        className="flex items-baseline justify-between px-3 py-2 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <h2 className="display text-base">섹터 강도 랭킹</h2>
        <div className="flex items-center gap-2 text-xs">
          <span className="opacity-60">{loading ? "로딩…" : data?.as_of}</span>
          <div className="flex">
            <button
              onClick={() => setMethod("detrended")}
              style={{
                background: method === "detrended" ? "var(--accent)" : "transparent",
                color: method === "detrended" ? "var(--fg-on-accent, #000)" : "var(--fg)",
                border: "1px solid var(--border)",
                padding: "2px 10px",
                fontSize: 10,
                cursor: "pointer",
              }}
            >
              YoY% 모멘텀
            </button>
            <button
              onClick={() => setMethod("raw")}
              style={{
                background: method === "raw" ? "var(--accent)" : "transparent",
                color: method === "raw" ? "var(--fg-on-accent, #000)" : "var(--fg)",
                border: "1px solid var(--border)",
                borderLeft: "none",
                padding: "2px 10px",
                fontSize: 10,
                cursor: "pointer",
              }}
            >
              Raw level
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2" style={{ gap: 0 }}>
        <div style={{ borderRight: "1px solid var(--border)" }}>
          <div
            className="px-3 py-1 text-[10px] uppercase opacity-60"
            style={{ borderBottom: "1px solid var(--border)" }}
          >
            ▲ TOP 5 강세
          </div>
          {top5.map((r) => (
            <Row key={r.sector_num} r={r} onClick={() => onSectorClick?.(r.sector_num)} />
          ))}
        </div>
        <div>
          <div
            className="px-3 py-1 text-[10px] uppercase opacity-60"
            style={{ borderBottom: "1px solid var(--border)" }}
          >
            ▼ BOTTOM 5 약세
          </div>
          {bot5.map((r) => (
            <Row key={r.sector_num} r={r} onClick={() => onSectorClick?.(r.sector_num)} />
          ))}
        </div>
      </div>
    </div>
  );
}
