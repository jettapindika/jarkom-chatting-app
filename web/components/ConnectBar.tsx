"use client";

import { useState } from "react";

import { useBridge } from "@/lib/bridge";

const SESSION_TEXT: Record<string, string> = {
  idle: "Belum masuk",
  connecting: "Menghubungi server chat…",
  connected: "Terhubung",
  closed: "Sesi berakhir",
};

export function ConnectBar() {
  const bridge = useBridge();
  const [draft, setDraft] = useState("");
  const connected = bridge.session === "connected";
  const busy = bridge.session === "connecting";
  const usable = bridge.link === "open";

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (connected) bridge.disconnect();
    else if (usable && !busy && draft.trim()) bridge.connect(draft.trim());
  }

  return (
    <form onSubmit={onSubmit} className="connect-form">
      {connected ? (
        <div className="session-identity">
          <span className="avatar" aria-hidden>{bridge.nick.slice(0, 2)}</span>
          <div className="min-w-0">
            <p className="session-name">{bridge.nick}</p>
            {bridge.sessionId ? (
              <p className="session-id" title={bridge.sessionId}>sesi {bridge.sessionId.slice(0, 8)}</p>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="nickname-field">
          <label htmlFor="nick">Nickname</label>
          <input
            id="nick"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={busy}
            maxLength={24}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="Pilih nama kamu"
          />
        </div>
      )}
      <button
        type="submit"
        disabled={!usable || busy || (!connected && draft.trim().length === 0)}
        className={connected ? "button-secondary" : "button-primary"}
      >
        {connected ? "Keluar" : busy ? "Menghubungi…" : "Masuk chat"}
      </button>
      <p role="status" className="connection-status">
        <span className={`status-dot${connected ? " is-connected" : ""}`} aria-hidden />
        {usable ? SESSION_TEXT[bridge.session] ?? bridge.session : "Bridge belum terhubung"}
      </p>
    </form>
  );
}

export function Roster() {
  const bridge = useBridge();
  const connected = bridge.session === "connected";

  return (
    <section aria-labelledby="roster-heading">
      <div className="roster-heading">
        <h2 id="roster-heading" className="section-label">User aktif</h2>
        <span className="roster-count" aria-label={`${bridge.users.length} user aktif`}>{bridge.users.length}</span>
      </div>
      {bridge.users.length === 0 ? (
        <p className="roster-empty">
          {connected
            ? "Belum ada daftar user. Ketik /list untuk memuatnya."
            : "Siapa saja yang online? Daftarnya muncul setelah kamu masuk."}
        </p>
      ) : (
        <ul className="roster-list">
          {bridge.users.map((name) => (
            <li key={name} className="roster-user">
              <span className="avatar" aria-hidden>{name.slice(0, 2)}</span>
              <div className="min-w-0">
                <p className="roster-user-name">{name}</p>
                <p className="roster-user-note">{name === bridge.nick ? "Kamu · online" : "Online"}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ErrorNotice() {
  const bridge = useBridge();
  if (!bridge.error) return null;

  return (
    <div role="alert" className="notice notice-error">
      <p><strong className="block font-semibold">Gagal terhubung</strong>{bridge.error}</p>
      <button type="button" onClick={bridge.dismissError}>Tutup</button>
    </div>
  );
}

export function LinkNotice() {
  const bridge = useBridge();
  if (bridge.link === "open") return null;

  return (
    <div role="status" className="notice">
      {bridge.link === "connecting" ? (
        <p>Menghubungi bridge di <code className="notice-code">{bridge.url}</code>…</p>
      ) : (
        <p>
          Bridge belum bisa dihubungi di <code className="notice-code">{bridge.url}</code>.
          Jalankan server chat, lalu <code className="notice-code">python -m bridge.main</code>.
        </p>
      )}
      {bridge.link === "closed" ? (
        <button type="button" onClick={bridge.reconnect}>Coba lagi</button>
      ) : null}
    </div>
  );
}
