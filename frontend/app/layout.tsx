import type { Metadata } from "next";
import "./globals.css";
import Shell from "@/components/Shell";
import { ActorProvider } from "@/lib/actor";
import { THEME_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "Zoiko Assure",
  description: "Regulatory obligation and proof infrastructure for multi-entity, multi-jurisdiction enterprises",
  icons: { icon: "/logo.png" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // THEME_SCRIPT sets data-theme on <html> before first paint, so the server's markup cannot match it.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
      </head>
      <body>
        <ActorProvider>
          <Shell>{children}</Shell>
        </ActorProvider>
      </body>
    </html>
  );
}
