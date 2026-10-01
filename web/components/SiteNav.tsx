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
    <header className="site-header">
      <div className="site-header-inner">
        <div className="brand">
          <span className="brand-mark" aria-hidden>j.</span>
          <div>
            <p className="brand-name">jarkom<span className="font-normal text-muted"> / chat</span></p>
            <p className="brand-caption">Percakapan lewat socket TCP</p>
          </div>
        </div>

        <nav aria-label="Halaman" className="page-nav">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={active ? "nav-active" : undefined}
              >
                <span className="nav-symbol" aria-hidden>{link.href === "/" ? "#" : "≋"}</span>
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="theme-toggle">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
