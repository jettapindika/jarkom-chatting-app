"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { usersFrom, type Envelope, type TimelineEntry } from "./messages";
import type { TraceEvent } from "./trace";

/*
  Where the bridge lives. Both values come from the environment so no host or
  port is baked into the bundle; the defaults match the bridge's own defaults.
*/
const BRIDGE_HOST = process.env.NEXT_PUBLIC_CHAT_BRIDGE_HOST ?? "127.0.0.1";
const BRIDGE_PORT = process.env.NEXT_PUBLIC_CHAT_BRIDGE_PORT ?? "8787";

export const BRIDGE_URL = `ws://${BRIDGE_HOST}:${BRIDGE_PORT}`;

/** Transport link to the bridge process, independent of any chat session. */
export type LinkState = "connecting" | "open" | "closed";

/** Chat session on the other side of the bridge. */
export type SessionState = "idle" | "connecting" | "connected" | "closed";

/* Bound the transcript so a long session cannot grow without limit. */
const MAX_TIMELINE = 1000;
const MAX_TRACES = 600;
const VISIBLE_MESSAGE_TYPES: Record<string, true> = {
  BROADCAST: true,
  PRIVATE: true,
  USER_LIST: true,
  USER_JOIN: true,
  USER_LEAVE: true,
  NICK_OK: true,
  ERROR: true,
  DISCONNECT: true,
};
function isLocalActionLine(text: string): boolean {
  return (
    text.startsWith("Commands:\n") ||
    text.startsWith("*** perintah tidak dikenal.") ||
    text.startsWith("*** nickname tidak valid:")
  );
}


export interface BridgeApi {
  url: string;
  link: LinkState;
  session: SessionState;
  nick: string;
  sessionId: string | null;
  /** One ordered, capped transcript of local lines and visible envelopes. */
  timeline: TimelineEntry[];
  traces: TraceEvent[];
  users: string[];
  error: string | null;
  connect: (nick: string) => void;
  disconnect: () => void;
  send: (text: string) => void;
  reconnect: () => void;
  dismissError: () => void;
  clearTraces: () => void;
}

const BridgeContext = createContext<BridgeApi | null>(null);

export function BridgeProvider({ children }: { children: React.ReactNode }) {
  const [link, setLink] = useState<LinkState>("connecting");
  const [session, setSession] = useState<SessionState>("idle");
  const [nick, setNick] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [users, setUsers] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const nicknameRef = useRef("");
  const sequenceRef = useRef(0);
  /*
    Every socket gets a generation number. A frame from a socket that has since
    been replaced is ignored, which is what keeps a slow close during a
    reconnect from writing stale state over the live one.
  */
  const generationRef = useRef(0);

  const push = useCallback(<T,>(setter: React.Dispatch<React.SetStateAction<T[]>>, item: T, cap: number) => {
    setter((previous) => {
      const next = [...previous, item];
      return next.length > cap ? next.slice(next.length - cap) : next;
    });
  }, []);

  const appendTimeline = useCallback((entry: TimelineEntry) => {
    setTimeline((previous) => {
      const next = [...previous, entry];
      return next.length > MAX_TIMELINE ? next.slice(next.length - MAX_TIMELINE) : next;
    });
  }, []);

  const handleFrame = useCallback(
    (raw: string) => {
      let frame: Record<string, unknown>;
      try {
        frame = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        setError("Bridge mengirim data yang tidak bisa dibaca.");
        return;
      }

      switch (frame.type) {
        case "state": {
          const next = String(frame.state) as SessionState;
          if (typeof frame.nick === "string") {
            nicknameRef.current = frame.nick;
            setNick(frame.nick);
          }
          if (next !== "connected") setUsers([]);
          setSession(next);
          /* sessionId is present only once the handshake succeeded. */
          setSessionId(typeof frame.sessionId === "string" ? frame.sessionId : null);
          setError(null);
          break;
        }
        case "line": {
          const text = String(frame.text ?? "");
          if (!isLocalActionLine(text)) break;
          const sequence = sequenceRef.current++;
          appendTimeline({ kind: "line", sequence, text });
          break;
        }
        case "message": {
          const envelope = frame.message as Envelope;
          if (VISIBLE_MESSAGE_TYPES[envelope.type] !== true) break;

          const own = envelope.sender === nicknameRef.current;
          const sequence = sequenceRef.current++;
          appendTimeline({ kind: "message", sequence, message: envelope, own });

          if (envelope.type === "USER_LIST") {
            setUsers(usersFrom(envelope.payload));
          } else if (envelope.type === "USER_JOIN") {
            const joined = envelope.payload.nick;
            if (typeof joined === "string" && joined) {
              setUsers((previous) =>
                previous.includes(joined) ? previous : [...previous, joined],
              );
            }
          } else if (envelope.type === "USER_LEAVE") {
            const left = envelope.payload.nick;
            if (typeof left === "string") {
              setUsers((previous) => previous.filter((name) => name !== left));
            }
          } else if (envelope.type === "NICK_OK") {
            const nextNick = envelope.payload.nick;
            if (typeof nextNick === "string" && nextNick) {
              const previousNick = nicknameRef.current;
              nicknameRef.current = nextNick;
              setNick(nextNick);
              setUsers((previous) =>
                previous.map((name) => (name === previousNick ? nextNick : name)),
              );
            }
          } else if (envelope.type === "DISCONNECT") {
            setUsers([]);
            setSession("closed");
            setSessionId(null);
          }
          break;
        }
        case "trace": {
          push(setTraces, frame.event as TraceEvent, MAX_TRACES);
          break;
        }
        case "error": {
          setError(String(frame.message ?? "Kesalahan tidak dikenal."));
          break;
        }
        default:
          break;
      }
    },
    [appendTimeline, push],
  );

  const openSocket = useCallback(() => {
    generationRef.current += 1;
    const generation = generationRef.current;

    socketRef.current?.close();
    socketRef.current = null;

    setLink("connecting");
    setError(null);
    setSession("idle");
    setSessionId(null);
    setUsers([]);

    let socket: WebSocket;
    try {
      socket = new WebSocket(BRIDGE_URL);
    } catch {
      setLink("closed");
      setError(`Tidak bisa membuka ${BRIDGE_URL}.`);
      return;
    }
    socketRef.current = socket;

    socket.onopen = () => {
      if (generationRef.current !== generation) return;
      setLink("open");
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      if (generationRef.current !== generation) return;
      handleFrame(event.data);
    };

    socket.onerror = () => {
      if (generationRef.current !== generation) return;
      setError(
        `Tidak dapat menghubungi bridge di ${BRIDGE_URL}. Jalankan: python -m bridge.main`,
      );
    };

    socket.onclose = () => {
      if (generationRef.current !== generation) return;
      setLink("closed");
      setSession("idle");
      setSessionId(null);
      setUsers([]);
    };
  }, [handleFrame]);

  useEffect(() => {
    openSocket();
    return () => {
      /* Bump first: the closing socket's late events must not touch state. */
      generationRef.current += 1;
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [openSocket]);

  const connect = useCallback((requested: string) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("Bridge belum tersambung. Coba sambungkan ulang.");
      return;
    }
    setError(null);
    setSession("connecting");
    socket.send(JSON.stringify({ type: "connect", nick: requested }));
  }, []);

  const disconnect = useCallback(() => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "disconnect" }));
  }, []);

  const send = useCallback((text: string) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("Bridge belum tersambung. Coba sambungkan ulang.");
      return;
    }
    socket.send(JSON.stringify({ type: "input", text }));
  }, []);

  const dismissError = useCallback(() => setError(null), []);
  const clearTraces = useCallback(() => setTraces([]), []);

  const api = useMemo<BridgeApi>(
    () => ({
      url: BRIDGE_URL,
      link,
      session,
      nick,
      sessionId,
      timeline,
      traces,
      users,
      error,
      connect,
      disconnect,
      send,
      reconnect: openSocket,
      dismissError,
      clearTraces,
    }),
    [
      link,
      session,
      nick,
      sessionId,
      timeline,
      traces,
      users,
      error,
      connect,
      disconnect,
      send,
      openSocket,
      dismissError,
      clearTraces,
    ],
  );

  return <BridgeContext.Provider value={api}>{children}</BridgeContext.Provider>;
}

export function useBridge(): BridgeApi {
  const api = useContext(BridgeContext);
  if (!api) {
    throw new Error("useBridge dipakai di luar BridgeProvider");
  }
  return api;
}
