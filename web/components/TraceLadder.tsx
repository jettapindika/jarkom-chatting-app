"use client";

import { useBridge } from "@/lib/bridge";
import {
  LAYER_COLOR,
  LAYER_LABEL,
  LAYER_ORDER,
  LAYER_ROLE,
  formatBytes,
  formatClock,
  groupByTrace,
  shortId,
  type Direction,
  type Layer,
  type TraceEvent,
} from "@/lib/trace";

const DIRECTION_TEXT: Record<Direction, string> = {
  outbound: "dikirim ke server",
  inbound: "diterima dari server",
};

/* The arrow states which way the PDU travels, which is the fact the ladder
   exists to show. It is a data-flow marker, not a button ornament. */
const DIRECTION_ARROW: Record<Direction, string> = {
  outbound: "\u2191",
  inbound: "\u2193",
};

/** One layer's line in the ladder. */
function LayerRow({ event, last }: { event: TraceEvent; last: boolean }) {
  const color = LAYER_COLOR[event.layer];

  return (
    <li className="flex gap-3">
      {/* The rail: a swatch in the layer's series color, linked to the next
          layer by a hairline. The color is the legend key, not decoration. */}
      <span aria-hidden className="relative flex w-3 shrink-0 justify-center">
        {last ? null : (
          <span className="absolute inset-y-0 w-px bg-line" />
        )}
        <span
          className="relative mt-[5px] h-2.5 w-2.5 shrink-0 rounded-[2px]"
          style={{ background: color }}
        />
      </span>

      <div className="min-w-0 flex-1 pb-2.5">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-mono text-xs" style={{ color }}>
            L{event.layer}
          </span>
          <span className="text-xs text-muted">{LAYER_LABEL[event.layer]}</span>
          <span className="font-mono text-xs text-muted">{event.pduType}</span>
          <span className="ml-auto font-mono text-xs tabular-nums text-muted">
            {formatBytes(event.sizeBytes)}
          </span>
        </div>

        <p className="mt-0.5 break-words text-sm">{event.summary}</p>

        {event.payloadPreview ? (
          <p className="mt-0.5 break-words font-mono text-xs text-muted">
            {event.payloadPreview}
          </p>
        ) : null}

        {event.payloadHex ? (
          <details className="mt-1">
            <summary className="cursor-pointer text-xs text-muted hover:text-ink">
              byte
            </summary>
            <p className="mt-1 break-all font-mono text-xs leading-relaxed text-muted">
              {event.payloadHex}
            </p>
          </details>
        ) : null}
      </div>
    </li>
  );
}

/** One message's full descent or ascent through the four implemented layers. */
function TraceGroupCard({
  traceId,
  direction,
  timestamp,
  events,
  totalBytes,
}: {
  traceId: string;
  direction: Direction;
  timestamp: string;
  events: TraceEvent[];
  totalBytes: number;
}) {
  return (
    <li className="border border-control-line bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-line pb-2">
        <span className="font-mono text-xs text-accent">
          {DIRECTION_ARROW[direction]} {DIRECTION_TEXT[direction]}
        </span>
        <span className="font-mono text-xs tabular-nums text-muted">
          {formatClock(timestamp)}
        </span>
        <span className="font-mono text-xs text-muted" title={traceId}>
          {shortId(traceId)}
        </span>
        {totalBytes > 0 ? (
          <span className="ml-auto font-mono text-xs tabular-nums text-muted">
            {formatBytes(totalBytes)} di lapisan Transport
          </span>
        ) : null}
      </div>

      <ol className="mt-2">
        {events.map((event, index) => (
          <LayerRow
            key={`${event.layer}-${index}`}
            event={event}
            last={index === events.length - 1}
          />
        ))}
      </ol>
    </li>
  );
}

/** How many events each layer has carried this session. */
function LayerTotals({ traces }: { traces: TraceEvent[] }) {
  const totals = new Map<Layer, number>();
  for (const trace of traces) {
    totals.set(trace.layer, (totals.get(trace.layer) ?? 0) + 1);
  }

  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-1">
      {LAYER_ORDER.map((layer) => (
        <div key={layer} className="flex items-baseline gap-2">
          <dt className="font-mono text-xs" style={{ color: LAYER_COLOR[layer] }}>
            L{layer}
          </dt>
          <dd className="font-mono text-sm tabular-nums">
            {totals.get(layer) ?? 0}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** The live ladder: every trace event the bridge emitted, grouped per message. */
export function TraceLadder() {
  const bridge = useBridge();
  const groups = groupByTrace(bridge.traces).reverse();

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3 border border-control-line bg-surface px-3 py-2.5">
        <div>
          <h2 className="text-sm font-semibold">Event per lapisan</h2>
          <div className="mt-1.5">
            <LayerTotals traces={bridge.traces} />
          </div>
        </div>

        <div className="text-right">
          <button
            type="button"
            onClick={bridge.clearTraces}
            disabled={bridge.traces.length === 0}
            className="rounded-sm border border-control-line px-3 py-1.5 text-xs transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
          >
            Kosongkan
          </button>
          <p className="mt-1 text-xs text-muted">
            {bridge.traces.length} event, {groups.length} pesan
          </p>
        </div>
      </div>

      <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
        {LAYER_ORDER.map((layer) => (
          <div key={layer} className="flex items-baseline gap-2">
            <dt className="font-mono text-xs" style={{ color: LAYER_COLOR[layer] }}>
              L{layer}
            </dt>
            <dd className="text-xs text-muted">
              <span className="text-ink">{LAYER_LABEL[layer]}</span>:{" "}
              {LAYER_ROLE[layer]}
            </dd>
          </div>
        ))}
      </dl>

      {groups.length === 0 ? (
        <p className="border border-control-line bg-surface px-3 py-6 text-sm text-muted">
          Belum ada event. Setiap perintah yang kamu kirim melewati empat
          lapisan, dan setiap lapisan melaporkan apa yang dilakukannya di sini.
          Lapisan 3 sampai 1 dipegang sistem operasi, jadi tidak muncul.
        </p>
      ) : (
        <ol className="space-y-3">
          {groups.map((group) => (
            <TraceGroupCard
              key={group.traceId}
              traceId={group.traceId}
              direction={group.direction}
              timestamp={group.timestamp}
              events={group.events}
              totalBytes={group.totalBytes}
            />
          ))}
        </ol>
      )}
    </div>
  );
}
