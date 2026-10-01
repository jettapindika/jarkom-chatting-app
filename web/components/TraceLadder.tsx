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
  outbound: "keluar ke server",
  inbound: "masuk dari server",
};

/* The arrow marks packet direction, which is the ladder's data signal. */
const DIRECTION_ARROW: Record<Direction, string> = {
  outbound: "\u2191",
  inbound: "\u2193",
};

/** One layer's line in the ladder. */
function LayerRow({ event, last }: { event: TraceEvent; last: boolean }) {
  const color = LAYER_COLOR[event.layer];

  return (
    <li className="flex gap-3">
      <span aria-hidden className="relative flex w-3 shrink-0 justify-center">
        {last ? null : <span className="absolute inset-y-0 w-px bg-line" />}
        <span
          className="relative mt-1.5 h-2.5 w-2.5 shrink-0 rounded-[2px]"
          style={{ background: color }}
        />
      </span>

      <div className="min-w-0 flex-1 pb-4">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-mono text-xs font-semibold" style={{ color }}>
            L{event.layer}
          </span>
          <span className="text-xs text-muted">{LAYER_LABEL[event.layer]}</span>
          <span className="font-mono text-xs text-muted">{event.pduType}</span>
          <span className="ml-auto font-mono text-xs tabular-nums text-muted">
            {formatBytes(event.sizeBytes)}
          </span>
        </div>

        <p className="mt-1 break-words text-sm leading-5">{event.summary}</p>

        {event.payloadPreview ? (
          <p className="mt-1 break-all font-mono text-xs leading-5 text-muted">
            {event.payloadPreview}
          </p>
        ) : null}

        {event.payloadHex ? (
          <details className="mt-1">
            <summary className="flex min-h-11 cursor-pointer items-center text-xs text-muted underline decoration-line underline-offset-4 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              Lihat byte
            </summary>
            <p className="break-all font-mono text-xs leading-relaxed text-muted">
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
    <li className="panel rounded-[14px] border border-line p-4 sm:p-5">
      <header className="flex flex-wrap items-start gap-x-3 gap-y-2 border-b border-line pb-3">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs font-semibold text-accent">
            {DIRECTION_ARROW[direction]} {DIRECTION_TEXT[direction]}
          </p>
          <p className="mt-1 break-all font-mono text-xs text-muted" title={traceId}>
            Jejak {shortId(traceId)}
          </p>
        </div>
        <div className="text-left sm:text-right">
          <p className="font-mono text-xs tabular-nums text-muted">
            {formatClock(timestamp)}
          </p>
          {totalBytes > 0 ? (
            <p className="mt-1 font-mono text-xs tabular-nums text-muted">
              {formatBytes(totalBytes)} di Transport
            </p>
          ) : null}
        </div>
      </header>

      <ol className="mt-4">
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
    <dl className="flex flex-wrap gap-x-5 gap-y-2">
      {LAYER_ORDER.map((layer) => (
        <div key={layer} className="flex items-baseline gap-2">
          <dt className="font-mono text-xs font-semibold" style={{ color: LAYER_COLOR[layer] }}>
            L{layer}
          </dt>
          <dd className="font-mono text-sm tabular-nums">{totals.get(layer) ?? 0}</dd>
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
    <section className="space-y-4" aria-labelledby="trace-heading">
      <header className="panel rounded-[14px] border border-line p-4 sm:p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <p className="section-label">Paket nyata dari sesi</p>
            <h2 id="trace-heading" className="text-lg font-semibold tracking-tight">
              Tangga jejak
            </h2>
            <LayerTotals traces={bridge.traces} />
          </div>

          <div className="flex items-center gap-3 sm:flex-col sm:items-end sm:gap-2">
            <button
              type="button"
              onClick={bridge.clearTraces}
              disabled={bridge.traces.length === 0}
              className="button-secondary min-h-11 px-4 text-sm disabled:cursor-not-allowed disabled:opacity-50"
            >
              Kosongkan
            </button>
            <p className="text-xs text-muted">
              {bridge.traces.length} kejadian, {groups.length} pesan
            </p>
          </div>
        </div>
      </header>

      <dl className="panel grid gap-x-6 gap-y-3 rounded-[14px] border border-line p-4 sm:grid-cols-2 sm:p-5">
        {LAYER_ORDER.map((layer) => (
          <div key={layer} className="min-w-0">
            <dt className="font-mono text-xs font-semibold" style={{ color: LAYER_COLOR[layer] }}>
              L{layer} {LAYER_LABEL[layer]}
            </dt>
            <dd className="mt-1 text-xs leading-5 text-muted">{LAYER_ROLE[layer]}</dd>
          </div>
        ))}
      </dl>

      {groups.length === 0 ? (
        <div className="panel rounded-[14px] border border-line p-5 sm:p-6">
          <p className="text-sm font-medium">Belum ada jejak sesi.</p>
          <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
            Hubungkan sesi, lalu kirim pesan di Ruang chat. Kartu trace akan
            muncul setelah bridge menerima event dari pesan yang benar-benar
            berpindah.
          </p>
        </div>
      ) : (
        <ol className="space-y-4">
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
    </section>
  );
}
