// GAZUA root layout — minimal shell.
import type { Metadata } from "next";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "GAZUA — 가즈아에 데이터를 얹다",
  description: "WICS 34섹터 × 10지표 × 30년 매트릭스. 한강 물이 아닌, 한강뷰로.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link rel="preconnect" href="https://cdn.jsdelivr.net" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css"
        />
      </head>
      <body>
        <header style={{ borderBottom: "1px solid var(--border)", padding: "10px 16px" }}>
          <a href="/" style={{ display: "inline-block" }}>
            <strong className="display text-lg">GAZUA</strong>
            <span className="ml-2 text-xs opacity-60">가즈아에 데이터를 얹다</span>
          </a>
        </header>
        <main style={{ padding: "16px" }}>{children}</main>
        <footer style={{
          borderTop: "1px solid var(--border)",
          padding: "12px 16px",
          fontSize: 11,
          opacity: 0.6,
        }}>
          Apache 2.0 ·{" "}
          <a href="https://github.com/yuyongkim/GAZUA" target="_blank" rel="noreferrer">
            github.com/yuyongkim/GAZUA
          </a>{" "}
          · 가즈아는 야성, 데이터는 사다리.
        </footer>
      </body>
    </html>
  );
}
