import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kanon — Agent reliability",
  description: "Per-slice reliability and regression evidence for agent runs.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link className="brand" href="/" aria-label="Kanon results overview">
            <span className="brand-mark">K</span>
            <span>Kanon</span>
          </Link>
          <nav aria-label="Primary navigation">
            <Link href="/">Overview</Link>
            <Link href="/regression">Regression</Link>
          </nav>
          <span className="header-meta">health-insurance · subtle</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
