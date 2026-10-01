"use client";

import { ConnectBar, ErrorNotice, LinkNotice } from "@/components/ConnectBar";
import { TraceLadder } from "@/components/TraceLadder";
import { useBridge } from "@/lib/bridge";

/*
  The visualizer reads the trace events the bridge actually emitted while it
  moved real messages, so this page shows the protocol as it ran, not a drawing
  of how it is supposed to run.
*/
export default function VisualizerPage() {
  const bridge = useBridge();

  return (
    <div className="space-y-4">
      <section className="space-y-3 border border-control-line bg-surface px-3 py-3">
        <h1 className="text-sm font-semibold">Visualizer lapisan OSI</h1>
        <p className="max-w-2xl text-sm text-muted">
          Setiap pesan yang lewat dilaporkan oleh keempat lapisan yang kita
          tulis sendiri. Satu kotak di bawah berarti satu pesan, dan isinya
          perjalanan pesan itu dari lapisan atas ke lapisan bawah saat dikirim,
          atau sebaliknya saat diterima.
        </p>
        <p className="max-w-2xl text-sm text-muted">
          Saat menerima, jejak berhenti di L6. Yang mengerjakan L7 di sisi
          penerima adalah pemakai stack (client CLI atau bridge), bukan
          SessionCore, jadi lapisan itu memang tidak melaporkan apa pun. Jejak
          yang berpura-pura sebaliknya akan mengarang, bukan mengukur.
        </p>
        <ConnectBar />
        <LinkNotice />
        <ErrorNotice />
      </section>

      {bridge.link === "open" && bridge.session !== "connected" ? (
        <p className="border border-control-line bg-surface px-3 py-2 text-sm text-muted">
          Masuk dulu, lalu kirim pesan dari{" "}
          <a
            href="/"
            className="underline underline-offset-2 hover:text-ink"
          >
            Ruang chat
          </a>
          . Trace muncul di sini selagi pesan berpindah.
        </p>
      ) : null}

      <TraceLadder />
    </div>
  );
}
