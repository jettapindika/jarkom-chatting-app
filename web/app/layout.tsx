import type { Metadata } from "next";

import { SiteNav } from "@/components/SiteNav";
import { BridgeProvider } from "@/lib/bridge";

import "./globals.css";

export const metadata: Metadata = {
  title: "Jarkom Chat",
  description:
    "Chat client-server lewat socket TCP dengan protokol aplikasi buatan sendiri, plus visualizer lapisan OSI.",
};

/*
  Applied before first paint so a light-theme reader never sees a dark flash.
  Inline and tiny on purpose: it must not become a request on the critical path.
*/
const THEME_BOOTSTRAP = `try{var s=localStorage.getItem("jarkom-theme");var d=s?s==="dark":window.matchMedia("(prefers-color-scheme: dark)").matches;document.documentElement.classList.toggle("dark",d)}catch(e){}`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="id" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="min-h-screen">
        <BridgeProvider>
          <SiteNav />
          <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">{children}</main>
        </BridgeProvider>
      </body>
    </html>
  );
}
