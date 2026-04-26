// GAZUA — landing.
import Link from "next/link";

export default function HomePage() {
  return (
    <div className="max-w-3xl mx-auto py-12 px-4">
      <h1 className="display text-5xl mb-4">GAZUA</h1>
      <p className="text-xl opacity-80 mb-2">가즈아에 데이터를 얹다.</p>
      <p className="text-sm opacity-60 mb-10">한강 물이 아닌, 한강뷰로 가즈아.</p>

      <div className="grid gap-3">
        <Link
          href="/research/sector-indicators"
          className="rounded p-4 border hover:opacity-80"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}
        >
          <div className="display text-base mb-1">📊 매트릭스 + 분석</div>
          <div className="text-xs opacity-60">
            WICS 34섹터 × 10지표 × 30년. 강도 랭킹 · 백테스트 · 주도주 · 시계열.
          </div>
        </Link>

        <a
          href="https://github.com/yuyongkim/GAZUA"
          target="_blank"
          rel="noreferrer"
          className="rounded p-4 border hover:opacity-80"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}
        >
          <div className="display text-base mb-1">⭐ GitHub</div>
          <div className="text-xs opacity-60">
            Apache 2.0 — Self-host 가능. Issue / PR 환영.
          </div>
        </a>
      </div>

      <div className="mt-12 text-xs opacity-50 leading-6">
        가즈아는 야성, 데이터는 사다리. 야수가 사다리를 들면 한강뷰.
      </div>
    </div>
  );
}
