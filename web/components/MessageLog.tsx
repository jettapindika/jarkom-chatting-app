"use client";

import { useEffect, useRef, useState } from "react";

import { useBridge } from "@/lib/bridge";
import { formatMessageTime, systemMessageText, textFrom } from "@/lib/messages";

/**
 * The transcript. Chat bubbles come from structured envelopes the bridge
 * sends, not from parsing the CLI-rendered lines. Local bridge output with no
 * envelope (help text, rejected nicknames) appears as plain lines in the
 * same timeline.
 */
export function MessageLog() {
  const bridge = useBridge();
  const scrollRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const [awayFromEnd, setAwayFromEnd] = useState(false);
  const lastSequence = bridge.timeline.at(-1)?.sequence;

  useEffect(() => {
    const container = scrollRef.current;
    if (container && followingRef.current) container.scrollTop = container.scrollHeight;
  }, [lastSequence]);

  return (
    <div className="transcript-wrap">
      <div
        ref={scrollRef}
        className="transcript"
        role="log"
        aria-label="Percakapan"
        aria-relevant="additions text"
        tabIndex={0}
        onScroll={(event) => {
          const node = event.currentTarget;
          const following = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
          followingRef.current = following;
          setAwayFromEnd(!following);
        }}
      >
        {bridge.timeline.length === 0 ? (
          <div className="transcript-empty">
            <div className="empty-mark" aria-hidden>#</div>
            <h2>Obrolan dimulai di sini.</h2>
            <p>
              {bridge.session === "connected"
                ? "Kirim pesan pertamamu. Semua user yang terhubung akan menerimanya."
                : "Pilih nickname dan masuk untuk mengirim pesan ke teman yang terhubung."}
            </p>
          </div>
        ) : (
          <ol className="transcript-list">
            {bridge.timeline.map((entry) => {
              if (entry.kind === "line") {
                return (
                  <li key={entry.sequence} className="system-row local-output">
                    <pre>{entry.text}</pre>
                  </li>
                );
              }

              const message = entry.message;
              if (message.type !== "BROADCAST" && message.type !== "PRIVATE") {
                return (
                  <li key={entry.sequence} className={`system-row${message.type === "ERROR" ? " is-error" : ""}`}>
                    {systemMessageText(message)}
                  </li>
                );
              }

              const privateMessage = message.type === "PRIVATE";
              const incomingPrivate = privateMessage && !entry.own && !("to" in message.payload);
              const target = typeof message.payload.to === "string" ? message.payload.to : "";

              return (
                <li key={entry.sequence} className={`message-row${entry.own ? " is-own" : ""}`}>
                  {!entry.own ? <span className="avatar" aria-hidden>{message.sender.slice(0, 2)}</span> : null}
                  <div className="message-content">
                    <div className="message-meta">
                      <span className="message-sender">{entry.own ? "Kamu" : message.sender}</span>
                    </div>
                    <div className="message-bubble">
                      {privateMessage ? (
                        <span className="private-label">
                          {incomingPrivate ? "Pesan pribadi untuk kamu" : target ? `Pribadi ke ${target}` : "Pesan pribadi"}
                        </span>
                      ) : null}
                      <p>{textFrom(message.payload)}</p>
                      <p className="message-time">
                        <time dateTime={message.timestamp} title={message.timestamp}>{formatMessageTime(message.timestamp)}</time>
                      </p>
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
      {awayFromEnd ? (
        <button
          type="button"
          className="button-secondary jump-latest"
          onClick={() => {
            const container = scrollRef.current;
            followingRef.current = true;
            if (container) container.scrollTop = container.scrollHeight;
            setAwayFromEnd(false);
          }}
        >
          Ke pesan terbaru ↓
        </button>
      ) : null}
    </div>
  );
}

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
    <form onSubmit={onSubmit} className="composer">
      <div className="composer-field">
        <label htmlFor="command" className="sr-only">Pesan atau perintah</label>
        <input
          id="command"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={!connected}
          autoComplete="off"
          placeholder={connected ? "Tulis pesan, atau /help…" : "Masuk dulu untuk menulis"}
          aria-describedby="composer-hint"
        />
        <button
          type="submit"
          disabled={!connected || value.trim().length === 0}
          className="button-primary send-button"
          aria-label="Kirim pesan"
          title="Kirim pesan"
        >
          <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m4 4 17 8-17 8 3-8-3-8Z M7 12h14" />
          </svg>
        </button>
      </div>
      <div className="composer-hint" id="composer-hint">
        <span><kbd>Enter</kbd> untuk kirim · <kbd>↑ ↓</kbd> riwayat input</span>
        <span>TCP / Jarkom</span>
      </div>
    </form>
  );
}
