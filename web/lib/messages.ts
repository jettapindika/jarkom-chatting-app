/** Protocol message shapes, mirroring the Python envelope. */

export interface Envelope {
  type: string;
  sender: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

/**
 * Nicknames out of a USER_LIST payload.
 *
 * The server sends `{"users": [{"nick": ..., "joined_at": ...}]}`. Entries
 * that do not carry a string `nick` are dropped rather than rendered as a
 * broken row, because this payload crosses a process boundary.
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
