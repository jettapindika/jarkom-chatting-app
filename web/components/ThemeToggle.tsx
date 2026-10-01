"use client";

import { useEffect, useState } from "react";

/**
 * Light/dark switch.
 *
 * Dark is the default because this tool is read next to a terminal and its main
 * content is a byte ladder where four layer colors must stay legible; a light
 * page is still a first-class mode, so the toggle is real, not a stub.
 */
export function ThemeToggle() {
  const [dark, setDark] = useState(true);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      window.localStorage.setItem("jarkom-theme", next ? "dark" : "light");
    } catch {
      /* Storage blocked: the toggle still applies to this page view. */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={dark}
      className="rounded-sm border border-control-line px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent hover:text-accent"
    >
      <span suppressHydrationWarning>
        {dark ? "Tema terang" : "Tema gelap"}
      </span>
    </button>
  );
}
