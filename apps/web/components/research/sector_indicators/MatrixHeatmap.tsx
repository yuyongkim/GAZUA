// MatrixHeatmap — 34 sectors × 10 indicators 그리드.
// 색상 6단계 (z-score 기반). 셀 클릭 → onCellClick 콜백.
"use client";

import type { MatrixCell } from "@/types/sector_indicators";

interface Props {
  cells: MatrixCell[];
  onCellClick?: (cell: MatrixCell) => void;
}

function bucketByZ(z: number | null): number {
  if (z === null || Number.isNaN(z)) return 0;
  if (z <= -2) return 1;
  if (z <= -1) return 2;
  if (z < 0) return 3;
  if (z < 1) return 4;
  if (z < 2) return 5;
  return 6;
}

function fmtNum(v: number | null): string {
  if (v === null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  if (a >= 100) return v.toFixed(0);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

function fmtPct(v: number | null): string {
  if (v === null || Number.isNaN(v)) return "";
  const sign = v > 0 ? "+" : "";
  if (Math.abs(v) >= 1000) return sign + (v / 1).toFixed(0) + "%";
  return sign + v.toFixed(1) + "%";
}

const BUCKET_BG = [
  "var(--surface-2)", // 0 missing
  "#7a1f1f",          // 1 strong neg
  "#9a3535",          // 2 mid neg
  "#7a4040",          // 3 mild neg
  "#3a5a40",          // 4 mild pos
  "#2d7a44",          // 5 mid pos
  "#1f9a4a",          // 6 strong pos
];

const BUCKET_FG = [
  "var(--fg-muted)",
  "#fff", "#fff", "#fff", "#e8f5e9", "#fff", "#fff",
];

export function MatrixHeatmap({ cells, onCellClick }: Props) {
  // Group by sector (sector_num → 10 cells)
  const bySector = new Map<string, MatrixCell[]>();
  for (const c of cells) {
    if (!bySector.has(c.sector_num)) bySector.set(c.sector_num, []);
    bySector.get(c.sector_num)!.push(c);
  }
  // Sort cells within each sector by idx
  for (const arr of bySector.values()) {
    arr.sort((a, b) => a.idx - b.idx);
  }
  const sortedSectors = [...bySector.keys()].sort();

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] border-collapse">
        <thead className="sticky top-0 z-10" style={{ background: "var(--surface)" }}>
          <tr>
            <th className="text-left px-2 py-1 border-b" style={{ borderColor: "var(--border)", minWidth: 110 }}>
              섹터 / 지표
            </th>
            {Array.from({ length: 10 }, (_, i) => (
              <th
                key={i}
                className="text-center px-1 py-1 border-b"
                style={{ borderColor: "var(--border)", minWidth: 70 }}
              >
                #{i + 1}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedSectors.map((sec) => {
            const row = bySector.get(sec)!;
            const sectorName = row[0]?.sector_name ?? "?";
            return (
              <tr key={sec} data-sector={sec}>
                <th
                  className="text-left px-2 py-1 align-middle border-b"
                  style={{
                    borderColor: "var(--border)",
                    background: "var(--surface)",
                    fontWeight: 500,
                  }}
                >
                  <span className="opacity-50 mr-1">[{sec}]</span>
                  {sectorName}
                </th>
                {row.map((c) => {
                  const b = bucketByZ(c.summary.z3y);
                  return (
                    <td
                      key={c.cell_id}
                      onClick={() => onCellClick?.(c)}
                      title={`${c.indicator}\n출처: ${c.source_raw}\n최근값: ${fmtNum(c.summary.latest)} (${c.summary.latest_date ?? "—"})\n12M%: ${fmtPct(c.summary.yoy_pct)}\n3Y z: ${c.summary.z3y?.toFixed(2) ?? "—"}\nrows: ${c.summary.rows}`}
                      className="border cursor-pointer hover:opacity-80 transition-opacity"
                      style={{
                        background: BUCKET_BG[b],
                        color: BUCKET_FG[b],
                        borderColor: "var(--border)",
                        padding: "6px 4px",
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                        verticalAlign: "top",
                      }}
                    >
                      <div style={{ fontSize: 10, opacity: 0.85, textAlign: "left" }}>
                        {c.indicator.length > 18 ? c.indicator.slice(0, 17) + "…" : c.indicator}
                      </div>
                      <div style={{ fontSize: 11, fontWeight: 600 }}>
                        {fmtNum(c.summary.latest)}
                      </div>
                      <div style={{ fontSize: 9, opacity: 0.75 }}>
                        {fmtPct(c.summary.yoy_pct)}
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-3 flex items-center gap-2 text-[10px]" style={{ color: "var(--fg-muted)" }}>
        <span>3Y z-score:</span>
        {[1, 2, 3, 4, 5, 6].map((b) => (
          <span key={b} className="px-2 py-0.5 rounded" style={{ background: BUCKET_BG[b], color: BUCKET_FG[b] }}>
            {b === 1 ? "≤-2" : b === 2 ? "-2~-1" : b === 3 ? "-1~0" : b === 4 ? "0~1" : b === 5 ? "1~2" : "≥2"}
          </span>
        ))}
        <span className="ml-3 px-2 py-0.5 rounded" style={{ background: BUCKET_BG[0], color: BUCKET_FG[0] }}>
          missing
        </span>
      </div>
    </div>
  );
}
