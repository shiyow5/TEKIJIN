import { AppHeader } from "@/components/AppHeader";
import type { Metadata } from "next";
import { Noto_Sans_JP } from "next/font/google";
import "./globals.css";

const notoSansJp = Noto_Sans_JP({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  display: "swap",
  variable: "--font-noto",
});

export const metadata: Metadata = {
  title: "TEKIJIN（たずねーる）",
  description: "社内の「訊きづらさ」を溶かす、質問と回答のマッチング支援ツール。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja" className={notoSansJp.variable}>
      <body className="bg-surface text-on-surface font-sans">
        <div className="mx-auto flex min-h-screen w-full max-w-content flex-col">
          <AppHeader />
          <main className="flex-1 px-margin py-lg">{children}</main>
        </div>
      </body>
    </html>
  );
}
