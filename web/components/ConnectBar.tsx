"use client";

import { useState } from "react";

import { useBridge } from "@/lib/bridge";

const SESSION_TEXT: Record<string, string> = {
  idle: "belum masuk",
  connecting: "menghubungi server chat",
  connected: "terhubung",
  closed: "terputus",
};

/** Nickname plus the one button that connects or disconnects. */
export function ConnectBar() {
  const bridge = useBridge();
  const [draft, setDraft] = useState("");

  const connected = bridge.session === "connected";
  const busy = bridge.session === "connecting";
  const usable = bridge.link === "open";

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (connected) bridge.disconnect();
    else bridge.connect(draft.trim());
  }

  return (
    <form
      onSubmit={onSubmit}
      className="flex flex-wrap items-end gap-x-4 gap-y-3"
    >
      <div>
        <label htmlFor="nick" className="mb-1 block text-xs text-muted">
          Nickname
        </label>
        <input
          id="nick"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={connected || busy}
          maxLength={24}
          autoComplete="off"
          placeholder="misalnya budi"
          className="w-40 rounded-sm border border-control-line bg-surface px-2 py-1.5 text-sm placeholder:text-muted disabled:opacity-60"
        />
      </div>

      <button
        type="submit"
        disabled={!usable || busy || (!connected && draft.trim().length === 0)}
        className="rounded-sm bg-accent px-4 py-1.5 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {connected ? "Keluar" : busy ? "Menghubungi" : "Masuk"}
      </button>

      <p role="status" aria-live="polite" className="text-sm text-muted">
        {SESSION_TEXT[bridge.session] ?? bridge.session}
        {connected ? (
          <>
            {" sebagai "}
            <span className="font-mono text-ink">{bridge.nick}</span>
            {bridge.sessionId ? (
              <span className="ml-2 font-mono text-xs">
                sesi {bridge.sessionId.slice(0, 8)}
              </span>
            ) : null}
          </>
        ) : null}
      </p>
    </form>
  );
}

/** Live roster, kept from USER_LIST plus the join and leave notices. */
export function Roster() {
  const bridge = useBridge();
  const connected = bridge.session === "connected";

  return (
    <section aria-labelledby="roster-heading">
      <h2 id="roster-heading" className="text-xs font-semibold text-muted">
        User aktif
      </h2>

      {bridge.users.length === 0 ? (
        <p className="mt-2 text-sm text-muted">
          {connected
            ? "Kamu sendiri di sini. Buka terminal lain dan masuk dengan nickname berbeda."
            : "Daftar muncul setelah kamu masuk."}
        </p>
      ) : (
        <ul className="mt-2 space-y-1">
          {bridge.users.map((name) => (
            <li key={name} className="flex items-baseline gap-2 text-sm">
              <span className="font-mono">{name}</span>
              {name === bridge.nick ? (
                <span className="text-xs text-muted">kamu</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Errors the bridge reported, in its own words. */
export function ErrorNotice() {
  const bridge = useBridge();
  if (!bridge.error) return null;

  return (
    <p
      role="alert"
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border border-control-line bg-surface px-3 py-2 text-sm text-danger"
    >
      {bridge.error}
      <button
        type="button"
        onClick={bridge.dismissError}
        className="text-xs text-muted underline underline-offset-2 hover:text-ink"
      >
        tutup
      </button>
    </p>
  );
}

/** Shown while the WebSocket to the bridge is not open. */
export function LinkNotice() {
  const bridge = useBridge();
  if (bridge.link === "open") return null;

  return (
    <p
      role="status"
      className="border border-control-line bg-surface px-3 py-2 text-sm text-muted"
    >
      {bridge.link === "connecting" ? (
        <>Menghubungi bridge di <code className="font-mono">{bridge.url}</code>.</>
      ) : (
        <>
          Bridge tidak jalan di <code className="font-mono">{bridge.url}</code>.
          Jalankan server chat, lalu bridge:{" "}
          <code className="font-mono">python -m bridge.main</code>
        </>
      )}
      {bridge.link === "closed" ? (
        <button
          type="button"
          onClick={bridge.reconnect}
          className="ml-3 text-xs text-muted underline underline-offset-2 hover:text-ink"
        >
          coba lagi
        </button>
      ) : null}
    </p>
  );
}
