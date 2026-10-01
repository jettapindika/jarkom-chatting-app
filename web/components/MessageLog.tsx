"use client";

import { useEffect, useRef, useState } from "react";

import { useBridge } from "@/lib/bridge";

/**
 * The transcript.
 *
 * The bridge already rendered each line through the same `format_message` the
 * CLI client uses, so this shows exactly what a terminal user would see rather
 * than a second, drifting rendering.
 */
export function MessageLog() {
  const bridge = useBridge();
  const endRef = useRef<HTMLDivElement>(null);
  const count = bridge.lines.length;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [count]);

  return (
    <section
      aria-label="Percakapan"
      className="min-h-0 flex-1 overflow-y-auto border border-control-line bg-surface px-3 py-3"
    >
      {count === 0 ? (
        <p className="text-sm text-muted">
          Belum ada pesan. Setelah masuk, tulis apa saja dan tekan Enter, atau
          mulai dengan <code className="font-mono">/help</code> untuk melihat
          perintah yang tersedia.
        </p>
      ) : (
        <ol className="space-y-0.5">
          {bridge.lines.map((line, index) => (
            /* Lines have no identity of their own and are append-only, so the
               index is stable for the lifetime of this list. */
            <li
              key={index}
              className="whitespace-pre-wrap break-words font-mono text-sm leading-relaxed"
            >
              {line}
            </li>
          ))}
        </ol>
      )}
      <div ref={endRef} />
    </section>
  );
}

/** Command input, with the same Up/Down history as the CLI client. */
export function CommandInput() {
  const bridge = useBridge();
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState(-1);

  const connected = bridge.session === "connected";

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const text = value.trim();
    if (!text || !connected) return;
    bridge.send(text);
    setHistory((previous) => [...previous, text]);
    setCursor(-1);
    setValue("");
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    if (history.length === 0) return;
    event.preventDefault();

    if (event.key === "ArrowUp") {
      const next = cursor === -1 ? history.length - 1 : Math.max(0, cursor - 1);
      setCursor(next);
      setValue(history[next]);
      return;
    }

    if (cursor === -1) return;
    const next = cursor + 1;
    if (next >= history.length) {
      setCursor(-1);
      setValue("");
    } else {
      setCursor(next);
      setValue(history[next]);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex items-center gap-2">
      <label htmlFor="command" className="sr-only">
        Pesan atau perintah
      </label>
      <input
        id="command"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        disabled={!connected}
        autoComplete="off"
        placeholder={
          connected ? "Tulis pesan, atau /help" : "Masuk dulu untuk menulis"
        }
        className="min-w-0 flex-1 rounded-sm border border-control-line bg-surface px-3 py-2 font-mono text-sm placeholder:font-sans placeholder:text-muted disabled:opacity-60"
      />
      <button
        type="submit"
        disabled={!connected || value.trim().length === 0}
        className="rounded-sm border border-control-line px-4 py-2 text-sm transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
      >
        Kirim
      </button>
    </form>
  );
}
