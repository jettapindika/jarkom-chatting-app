/**
 * Trace event types, mirroring the camelCase wire shape produced by
 * `trace.TraceEvent.to_dict()` in the Python bridge.
 */

export type Layer = 7 | 6 | 5 | 4;
export type Direction = "outbound" | "inbound";
export type TraceNode = "client" | "bridge" | "server";

export interface TraceEvent {
  traceId: string;
  sessionId: string | null;
  direction: Direction;
  layer: Layer;
  layerName: string;
  pduType: string;
  node: TraceNode;
  summary: string;
  payloadPreview: string;
  payloadHex: string;
  sizeBytes: number;
  timestamp: string;
}

/** Layers this project implements, top of the stack first. */
export const LAYER_ORDER: Layer[] = [7, 6, 5, 4];

export const LAYER_LABEL: Record<Layer, string> = {
  7: "Application",
  6: "Presentation",
  5: "Session",
  4: "Transport",
};

/** One line per layer saying what it actually does, for the visualizer legend. */
export const LAYER_ROLE: Record<Layer, string> = {
  7: "perintah dan aturan chat",
  6: "JSON dan UTF-8",
  5: "identitas sesi dan urutan",
  4: "socket TCP dan framing",
};

/** Tailwind color token per layer, defined in `globals.css`. */
export const LAYER_COLOR: Record<Layer, string> = {
  7: "var(--l7)",
  6: "var(--l6)",
  5: "var(--l5)",
  4: "var(--l4)",
};

/** One message's whole journey: the same `traceId` seen by several layers. */
export interface TraceGroup {
  traceId: string;
  direction: Direction;
  timestamp: string;
  events: TraceEvent[];
  totalBytes: number;
}

/**
 * Group events by trace id, keeping the layer order of travel.
 *
 * Outbound events descend L7 to L4; inbound events ascend L4 to L7. Sorting by
 * layer in the matching direction is what makes the ladder read top to bottom
 * the way a packet actually moves.
 */
export function groupByTrace(events: TraceEvent[]): TraceGroup[] {
  const groups = new Map<string, TraceGroup>();

  for (const event of events) {
    let group = groups.get(event.traceId);
    if (!group) {
      group = {
        traceId: event.traceId,
        direction: event.direction,
        timestamp: event.timestamp,
        events: [],
        totalBytes: 0,
      };
      groups.set(event.traceId, group);
    }
    group.events.push(event);
    if (event.layer === 4) {
      group.totalBytes = event.sizeBytes;
    }
  }

  for (const group of groups.values()) {
    const descending = group.direction === "outbound";
    group.events.sort((a, b) =>
      descending ? b.layer - a.layer : a.layer - b.layer,
    );
  }

  return [...groups.values()];
}

/** `2026-10-01T10:31:34.391Z` -> `17:31:34.391` in the reader's own clock. */
export function formatClock(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const hh = String(at.getHours()).padStart(2, "0");
  const mm = String(at.getMinutes()).padStart(2, "0");
  const ss = String(at.getSeconds()).padStart(2, "0");
  const ms = String(at.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${ms}`;
}

/** Shorten a UUID for display without losing which end it starts from. */
export function shortId(id: string | null, keep = 8): string {
  if (!id) return "belum ada";
  return id.length <= keep ? id : id.slice(0, keep);
}

/**
 * Byte counts in the units a reader compares at a glance.
 *
 * The ladder's whole point is relative PDU size between layers, so the numbers
 * have to be scannable rather than exact to the byte past a kilobyte.
 */
export function formatBytes(count: number): string {
  if (count < 1024) return `${count} B`;
  const kb = count / 1024;
  return `${kb.toFixed(kb < 10 ? 1 : 0)} kB`;
}
