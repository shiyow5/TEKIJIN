import { AppShell } from "@/components/AppShell";
import type { Metadata } from "next";
import "./globals.css";

// Font is provided via a CSS font stack (see globals.css `--font-noto`) rather
// than next/font/google, so `next build` never fetches over the network
// (network-independent build — technical-spec §1 principle 3).

export const metadata: Metadata = {
  title: "TEKIJIN",
  description: "社内の「訊きづらさ」を溶かす、質問と回答のマッチング支援ツール。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body className="bg-surface text-on-surface font-sans">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
