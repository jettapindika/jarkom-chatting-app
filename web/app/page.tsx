"use client";

import {
  ConnectBar,
  ErrorNotice,
  LinkNotice,
  Roster,
} from "@/components/ConnectBar";
import { CommandInput, MessageLog } from "@/components/MessageLog";
import { useBridge } from "@/lib/bridge";

/*
  The chat screen is one workspace: connect at the top, transcript in the
  middle, command box under it, roster on the side. Everything a session needs
  is on one screen, because a chat client that hides its roster behind a tab is
  hiding the one piece of state the user keeps checking.
*/
export default function ChatPage() {
  const bridge = useBridge();

  return (
    <div className="space-y-4">
      <div className="space-y-3 border border-control-line bg-surface px-3 py-3">
        <ConnectBar />
        <LinkNotice />
        <ErrorNotice />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_15rem]">
        <div className="flex h-[60vh] min-h-0 flex-col gap-2">
          <MessageLog />
          <CommandInput />
        </div>

        <aside className="border border-control-line bg-surface px-3 py-3">
          <Roster />

          <h2 className="mt-4 text-xs font-semibold text-muted">Perintah</h2>
          <dl className="mt-2 space-y-1.5 text-sm">
            {[
              ["/nick <nama>", "ganti nickname"],
              ["/list", "lihat user aktif"],
              ["/msg <user> <pesan>", "pesan pribadi"],
              ["/help", "semua perintah"],
              ["/quit", "keluar"],
            ].map(([command, meaning]) => (
              <div key={command}>
                <dt className="font-mono text-xs">{command}</dt>
                <dd className="text-xs text-muted">{meaning}</dd>
              </div>
            ))}
          </dl>

          <p className="mt-4 text-xs text-muted">
            Lapisan 7 sampai 4 dari tiap pesan bisa dilihat di{" "}
            <a
              href="/visualizer"
              className="underline underline-offset-2 hover:text-ink"
            >
              Visualizer OSI
            </a>
            .
          </p>

          {bridge.link !== "open" ? null : bridge.session !== "connected" ? (
            <p className="mt-4 text-xs text-muted">
              Kamu belum masuk, jadi perintah masih terkunci.
            </p>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
