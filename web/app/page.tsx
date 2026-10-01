"use client";

import Link from "next/link";

import { ConnectBar, ErrorNotice, LinkNotice, Roster } from "@/components/ConnectBar";
import { CommandInput, MessageLog } from "@/components/MessageLog";
import { useBridge } from "@/lib/bridge";

export default function ChatPage() {
  const bridge = useBridge();
  const connected = bridge.session === "connected";

  return (
    <div className="chat-workspace">
      <aside className="chat-sidebar" aria-label="Sesi dan user aktif">
        <section className="panel session-panel" aria-labelledby="session-heading">
          <div className="session-heading">
            <h2 id="session-heading">{connected ? "Sesi kamu" : "Ikut percakapan"}</h2>
            <p>{connected ? "Terhubung ke ruang bersama." : "Satu nickname, lalu mulai ngobrol."}</p>
          </div>
          <ConnectBar />
        </section>
        <div className="panel roster-panel"><Roster /></div>
      </aside>

      <section className="chat-column" aria-labelledby="chat-title">
        <header className="chat-heading">
          <span className="chat-heading-mark" aria-hidden>#</span>
          <div>
            <h1 className="chat-title" id="chat-title">Ruang bersama</h1>
            <p className="chat-subtitle">Pesan ke semua user · pesan pribadi lewat /msg</p>
          </div>
          <p className="chat-heading-state">
            <span className={`status-dot${connected ? " is-connected" : ""}`} aria-hidden />
            {connected ? "Terhubung" : "Belum masuk"}
          </p>
        </header>
        <div className="chat-notices"><LinkNotice /><ErrorNotice /></div>
        <MessageLog />
        <CommandInput />
      </section>

      <aside className="panel guide-panel" aria-labelledby="guide-title">
        <h2 id="guide-title" className="guide-title">Sedikit jalan pintas.</h2>
        <p className="guide-description">Ketik pesan untuk semua orang, atau pakai perintah ini.</p>
        <dl className="command-guide">
          {[
            ["/msg <user> <pesan>", "Kirim pesan hanya ke user tujuan."],
            ["/nick <nama>", "Ganti nickname dalam sesi ini."],
            ["/list", "Lihat daftar user yang online."],
            ["/help", "Tampilkan bantuan di percakapan."],
            ["/quit", "Akhiri sesi chat kamu."],
          ].map(([command, meaning]) => (
            <div key={command}>
              <dt>{command}</dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
        <div className="guide-footer">
          <p>Ingin melihat apa yang lewat di jaringan?</p>
          <Link href="/visualizer">Buka Visualizer OSI</Link>
          <p>Ikuti pesan di lapisan aplikasi, presentasi, sesi, dan transport.</p>
        </div>
      </aside>
    </div>
  );
}
