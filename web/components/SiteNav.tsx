"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "./ThemeToggle";

const LINKS = [
  { href: "/", label: "Ruang chat" },
  { href: "/visualizer", label: "Visualizer OSI" },
];

export function SiteNav() {
  const pathname = usePathname();

  return (
    <header className="border-b border-line">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
        <span className="text-sm font-semibold">
          Jarkom Chat
          <span className="ml-2 font-normal text-muted">
            socket TCP, protokol sendiri
          </span>
        </span>

        <nav aria-label="Halaman" className="flex items-center gap-1">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-sm px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "text-ink underline decoration-accent decoration-2 underline-offset-4"
                    : "text-muted hover:text-ink"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
