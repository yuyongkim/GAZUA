// Research · Sector Indicators — WICS 34섹터 × 10지표 매트릭스 히트맵.
"use client";

import { useEffect, useState } from "react";

import { BacktestPanel } from "@/components/research/sector_indicators/BacktestPanel";
import { CellDrawer } from "@/components/research/sector_indicators/CellDrawer";
import { LeaderStocksPanel } from "@/components/research/sector_indicators/LeaderStocksPanel";
import { MatrixHeatmap } from "@/components/research/sector_indicators/MatrixHeatmap";
import { StrengthRanking } from "@/components/research/sector_indicators/StrengthRanking";
import { apiBase } from "@/lib/apiBase";
import type { MatrixCell, MatrixResponse } from "@/types/sector_indicators";

export default function SectorIndicatorsPage() {
  const [matrix, setMatrix] = useState<MatrixResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [activeCell, setActiveCell] = useState<MatrixCell | null>(null);

  useEffect(() => {
    fetch(`${apiBase()}/api/v1/research/sector-indicators/matrix`)
      .then((r) => r.json())
      .then((b) => {
        if (b.ok && b.data) setMatrix(b.data as MatrixResponse);
        else setErr(b.error?.message ?? "no data");
      })
      .catch((e) => setErr(String(e?.message ?? e)));
  }, []);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <h1 className="display text-3xl">Sector Indicators</h1>
          <div className="text-xs opacity-60 mt-1">
            WICS 34섹터 × 10지표 = 340 셀 매트릭스 · 30년 시계열 · 셀 클릭 → 시계열 + 종목 리스트
          </div>
        </div>
        {matrix && (
          <div className="text-right">
            <div className="text-xs opacity-60">As of</div>
            <div className="text-sm font-mono">{matrix.as_of}</div>
            <div className="text-xs mt-1">
              <span className="font-semibold">{matrix.filled_cells}</span>
              <span className="opacity-60">/{matrix.total_cells}</span>
              <span className="ml-1 opacity-60">({matrix.fill_pct}%)</span>
            </div>
          </div>
        )}
      </div>

      {err && (
        <div
          className="p-4 rounded mb-3"
          style={{ background: "var(--surface-2)", color: "var(--fg-muted)" }}
        >
          데이터를 불러오지 못했습니다: {err}
          <button
            onClick={() => location.reload()}
            className="ml-3 px-3 py-1 text-xs"
            style={{ border: "1px solid var(--border)" }}
          >
            재시도
          </button>
        </div>
      )}

      {!matrix && !err && (
        <div className="text-sm opacity-60 py-8 text-center">매트릭스 로딩 중…</div>
      )}

      {matrix && (
        <>
          <StrengthRanking
            onSectorClick={(sec) => {
              const el = document.querySelector(`[data-sector="${sec}"]`);
              el?.scrollIntoView({ behavior: "smooth", block: "center" });
            }}
          />
          <BacktestPanel />
          <LeaderStocksPanel />
          <div
            className="rounded overflow-hidden"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <MatrixHeatmap cells={matrix.cells} onCellClick={setActiveCell} />
          </div>
        </>
      )}

      <CellDrawer cell={activeCell} onClose={() => setActiveCell(null)} />
    </div>
  );
}
