/** Protocol message shapes, mirroring the Python envelope. */
export interface Envelope {
  type: string;
  sender: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

export type TimelineEntry =
  | { kind: "message"; sequence: number; message: Envelope; own: boolean }
  | { kind: "line"; sequence: number; text: string };

/** Format a server timestamp in the reader's local clock. */
export function formatMessageTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "--:--:--";

  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

/** Render a readable notice for a system envelope, or null for chat frames. */
export function systemMessageText(message: Envelope): string | null {
  const payload = message.payload;

  switch (message.type) {
    case "USER_JOIN":
      return `*** ${typeof payload.nick === "string" ? payload.nick : "?"} bergabung`;
    case "USER_LEAVE":
      return `*** ${typeof payload.nick === "string" ? payload.nick : "?"} keluar`;
    case "USER_LIST": {
      const names = usersFrom(payload);
      if (names.length === 0) return "*** belum ada user online";
      return [`*** ${names.length} user online:`, ...names.map((name) => `    - ${name}`)].join(
        "\n",
      );
    }
    case "NICK_OK":
      return `*** nickname diganti menjadi ${typeof payload.nick === "string" ? payload.nick : "?"}`;
    case "ERROR": {
      const code = typeof payload.code === "string" ? payload.code : "ERROR";
      const detail = typeof payload.message === "string" ? payload.message : "";
      return `*** error [${code}] ${detail}`.trimEnd();
    }
    case "DISCONNECT":
      return "*** server menutup koneksi";
    default:
      return null;
  }
}

/**
 * Nicknames out of a USER_LIST payload.
 *
 * Entries that do not carry a string `nick` are dropped rather than rendered
 * as a broken row, because this payload crosses a process boundary.
 */
export function usersFrom(payload: Record<string, unknown>): string[] {
  const raw = payload.users;
  if (!Array.isArray(raw)) return [];

  const names: string[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null || !("nick" in entry)) continue;
    const nick = entry.nick;
    if (typeof nick === "string" && nick) names.push(nick);
  }
  return names;
}

/** The chat body of a message, or a placeholder when it is unreadable. */
export function textFrom(payload: Record<string, unknown>): string {
  const text = payload.text;
  return typeof text === "string" ? text : "(pesan tidak dapat dibaca)";
}

/** Plain-language names for wire types, for readers who do not know the enums. */
export const MESSAGE_LABEL: Record<string, string> = {
  BROADCAST: "pesan ke semua",
  PRIVATE: "pesan pribadi",
  USER_LIST: "daftar user",
  USER_JOIN: "user masuk",
  USER_LEAVE: "user keluar",
  NICK: "ganti nama",
  NICK_OK: "nama diganti",
  PING: "denyut",
  PONG: "balasan denyut",
  ERROR: "kesalahan",
  DISCONNECT: "keluar",
  CONNECT: "permintaan masuk",
  CONNECT_OK: "handshake diterima",
  CONNECT_ERR: "handshake ditolak",
};
