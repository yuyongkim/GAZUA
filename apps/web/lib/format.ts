// apps/web/lib/format.ts
// ---------------------------------------------------------------------------
// Shared number-formatting + lightweight series helpers.
//
// Ported from Company_Credit ``sepa/frontend/js/renderers/stock-profile.js``
// and ``sepa/frontend/js/core.js``. Only the client-side fallbacks live here
// — heavy math (moving averages, YoY etc.) should be done server-side when
// possible so every surface sees the same numbers. These helpers exist for
// (a) mini sparklines inside panes that already have the raw array, and
// (b) loading-state UI that doesn't wait for the server compute.
// ---------------------------------------------------------------------------

export function fmtNum(
  value: number | null | undefined,
  digits: number = 0,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(value: number | null | undefined, digits: number = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function fmtPlainPct(value: number | null | undefined, digits: number = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function fmtPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, {
    maximumFractionDigits: 0,
  });
}

/** Compact US-style formatter (1.2K, 3.4M, 5.6B). */
export function fmtCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(0)}K`;
  return `${Math.round(value)}`;
}

/** KRW compact (T/B/M with explicit currency tag). */
export function fmtKrwCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000_000) return `KRW ${(value / 1_000_000_000_000).toFixed(2)}T`;
  if (abs >= 1_000_000_000) return `KRW ${(value / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `KRW ${(value / 1_000_000).toFixed(1)}M`;
  return fmtPrice(value);
}

/** Korean 조/억/만 compact — matches FundamentalsPane's existing formatter. */
export function fmtKrwKorean(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toLocaleString(undefined, { maximumFractionDigits: 2 })}조`;
  if (abs >= 1e8) return `${(value / 1e8).toLocaleString(undefined, { maximumFractionDigits: 0 })}억`;
  if (abs >= 1e4) return `${(value / 1e4).toLocaleString(undefined, { maximumFractionDigits: 1 })}만`;
  return value.toLocaleString();
}

// ---------------------------------------------------------------------------
// Series helpers (client-side fallbacks)
// ---------------------------------------------------------------------------

/** Simple moving average — returns an array of the same length with nulls
 *  where the window is not yet full. Stable for small N; use sparingly. */
export function movingAvg(data: Array<number | null>, period: number): Array<number | null> {
  if (period <= 0) return data.slice();
  const out: Array<number | null> = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      out.push(null);
      continue;
    }
    let sum = 0;
    let missing = false;
    for (let j = i - period + 1; j <= i; j++) {
      const v = data[j];
      if (v == null || !Number.isFinite(v)) {
        missing = true;
        break;
      }
      sum += v as number;
    }
    out.push(missing ? null : sum / period);
  }
  return out;
}

/** Year-over-year percent change for an array of equal-cadence points.
 *  ``stride`` is how many indices back to compare (4 for quarterly, 1 for
 *  annual). Returns null where the reference point is missing or zero. */
export function yoyPct(
  data: Array<number | null>,
  stride: number = 4,
): Array<number | null> {
  const out: Array<number | null> = [];
  for (let i = 0; i < data.length; i++) {
    const cur = data[i];
    const prev = data[i - stride];
    if (cur == null || prev == null || !Number.isFinite(cur) || !Number.isFinite(prev) || prev === 0) {
      out.push(null);
      continue;
    }
    out.push(((cur - prev) / Math.abs(prev)) * 100);
  }
  return out;
}

/** Minimal sparkline — returns an SVG string.  Used only as a client-side
 *  fallback; the main chart is always Chartist / echarts.  Size is fixed to
 *  match the FundamentalsPane stat tiles. */
export function sparklineSvg(
  values: Array<number | null>,
  {
    width = 120,
    height = 32,
    stroke = "currentColor",
  }: { width?: number; height?: number; stroke?: string } = {},
): string {
  const xs = values
    .map((v, i) => ({ v, i }))
    .filter((p) => p.v != null && Number.isFinite(p.v));
  if (xs.length < 2) return "";
  const vals = xs.map((p) => p.v as number);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const toX = (i: number) => (i / (values.length - 1)) * (width - 2) + 1;
  const toY = (v: number) => height - 1 - ((v - min) / range) * (height - 2);
  const pts = xs.map((p) => `${toX(p.i).toFixed(1)},${toY(p.v as number).toFixed(1)}`);
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg"><path d="M${pts.join("L")}" fill="none" stroke="${stroke}" stroke-width="1.2"/></svg>`;
}
