"use client";

import Link from "next/link";

import { ConnectBar, ErrorNotice, LinkNotice } from "@/components/ConnectBar";
import { TraceLadder } from "@/components/TraceLadder";
import { useBridge } from "@/lib/bridge";

export default function VisualizerPage() {
  const bridge = useBridge();

  return (
    <div className="visualizer-page space-y-5">
      <section className="panel space-y-5 rounded-[20px] border border-line p-5 sm:p-6">
        <div className="space-y-2">
          <p className="section-label">Jejak jaringan</p>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
            Lihat pesan berjalan di empat lapisan
          </h1>
          <p className="max-w-2xl text-sm leading-6 text-muted">
            Trace berasal dari pesan nyata selama sesi, bukan data contoh.
            Setiap kartu menunjukkan arah, waktu, ukuran, dan urutan PDU.
          </p>
          <p className="max-w-2xl text-sm leading-6 text-muted">
            Pesan masuk berhenti di L6. L7 dikerjakan oleh client atau bridge,
            jadi jejak ini tidak mengarang penerusan yang tidak dilaporkan.
          </p>
        </div>

        <div className="space-y-3">
          <ConnectBar />
          <LinkNotice />
          <ErrorNotice />
        </div>
      </section>

      {bridge.link === "open" && bridge.session !== "connected" ? (
        <aside className="panel rounded-[14px] border border-line p-4 text-sm text-muted sm:p-5">
          <p>
            Masuk lalu kirim pesan dari{" "}
            <Link
              href="/"
              className="font-medium text-ink underline decoration-accent underline-offset-4 hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Ruang chat
            </Link>
            . Trace akan muncul saat pesan berpindah.
          </p>
        </aside>
      ) : null}

      <TraceLadder />
    </div>
  );
}
