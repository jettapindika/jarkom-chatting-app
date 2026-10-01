"use client";

import { useEffect, useState } from "react";

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
      aria-label={dark ? "Aktifkan tema terang" : "Aktifkan tema gelap"}
      title={dark ? "Tema terang" : "Tema gelap"}
      className="button-secondary"
    >
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        {dark ? (
          <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></>
        ) : (
          <path d="M20.5 14A8.8 8.8 0 0 1 10 3.5 8.8 8.8 0 1 0 20.5 14Z" />
        )}
      </svg>
      <span suppressHydrationWarning>
        {dark ? "Tema terang" : "Tema gelap"}
      </span>
    </button>
  );
}
