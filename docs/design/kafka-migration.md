# Design Note — The Path Past 100k Events/Day (Kafka Behind the SDK Seam)

> **Status: Design note — not an ADR.** ADR-0001 already names the escape
> hatch ("if event volume grows beyond ~100k/day, evaluate migrating the
> analytics pipeline to Kafka"). This note defines what that migration
> would have to preserve and which prerequisites are missing — it does
> **not** decide to migrate. Redis Streams remains the default and the
> recommendation for every current deployment.

- Date: 2026-06-12
- Relates to: [ADR-0001](../adr/0001-redis-streams-as-event-bus.md)
  (Redis Streams as event bus),
  [ADR-0005](../adr/0005-bus-dead-letter-semantics.md) (delivery semantics)

## The abstraction seam

The intended single chokepoint is
[`libs/openhis_sdk/src/openhis_sdk/bus.py`](../../libs/openhis_sdk/src/openhis_sdk/bus.py),
re-exported from
[`libs/openhis_sdk/src/openhis_sdk/__init__.py`](../../libs/openhis_sdk/src/openhis_sdk/__init__.py).
Its complete current API surface — i.e. everything a Kafka backend must
re-implement or map:

**Module constants**

| Name | Value | Meaning |
|---|---|---|
| `STREAM` | `openhis:events` | single event stream, `type` field routes |
| `MAXLEN` | `50_000` | approximate XADD trim — the retention policy |
| `DLQ_STREAM` | `openhis:events:dlq` | dead-letter stream (ADR-0005) |
| `DLQ_MAXLEN` | `10_000` | DLQ bound against poison floods |
| `EventHandler` | `Callable[[dict], Awaitable[None]]` | handler signature |

**Functions**

- `publish_event(client, event_type, payload)` — XADD with `type` +
  JSON `payload` fields, MAXLEN-trimmed. Note the seam leak: it takes an
  `aioredis.Redis` **client** as its first argument, so callers hold a
  Redis handle today.
- `send_to_dlq(client, group, entry_id, fields, error)` — parks
  `origin_id`/`type`/`payload`/`error`/`group` on the DLQ; **never raises**.

**`BusConsumer`** — constructor knobs and semantics (ADR-0005):

- `BusConsumer(redis_url, group, consumer, handlers, batch=20,
  block_ms=5_000, max_delivery=5, idle_ms=30_000, fallback_handler=None)`;
  single public method `run()` (blocking loop, cancellation-aware).
- **Ack-on-success**: XACK only after the handler returns; failures stay
  in the consumer group's PEL.
- **Retry**: XAUTOCLAIM redelivers entries idle ≥ `idle_ms` (own or a
  crashed peer's) on every loop pass.
- **Dead-letter**: after `max_delivery` failed deliveries (read back via
  XPENDING `times_delivered`) the entry goes to `DLQ_STREAM` and the
  original is acked so the group never wedges.
- **Unknown types are success**; `fallback_handler` receives the raw field
  mapping for typeless catch-all consumers (analytics uses exactly this in
  [`services/analytics/bus_consumer.py`](../../services/analytics/bus_consumer.py)).

### Prerequisite 0 — the seam is not yet airtight

Several services still `xadd` directly instead of calling the SDK:
[`services/hl7/handlers.py`](../../services/hl7/handlers.py),
[`services/integration-hub/app/bus.py`](../../services/integration-hub/app/bus.py),
[`services/mpi/bus.py`](../../services/mpi/bus.py) (sync client),
and [`services/admin/routers/identity.py`](../../services/admin/routers/identity.py).
Before any backend swap is even discussable, every
direct `xadd` of a domain event must be funneled through `publish_event`,
and `publish_event` itself should stop exposing the Redis client in its
signature (accept a URL/config-bound publisher object instead). This
refactor is worth doing regardless of Kafka — it is also what makes the
unit-test story (`tests/unit/sdk/test_bus.py`, fakeredis) authoritative.

## What a Kafka backend must preserve

| Redis Streams (today) | Kafka equivalent | Watch out |
|---|---|---|
| Consumer groups, independent cursors per service | Kafka consumer groups | direct mapping; group names carry over |
| At-least-once via ack-on-success | manual offset commit after handler success | Kafka commits *offsets*, not entries: one stuck message blocks the partition behind it. Per-message retry must be rebuilt (pause+seek, or a retry topic) — there is no PEL. |
| XAUTOCLAIM redelivery after `idle_ms` | rebalance redelivers uncommitted offsets; retry topic with delay for in-place retries | `idle_ms` has no native analogue; document the changed retry latency |
| `max_delivery` then DLQ (`openhis:events:dlq`) | delivery-attempt header + dead-letter **topic** `openhis.events.dlq` with the same fields (`origin_id`→offset, `type`, `payload`, `error`, `group`) | keep field names so existing DLQ tooling/replay docs (ADR-0005 §Operational notes) survive |
| `MAXLEN ~ 50_000` (count-bounded) | `retention.ms` / `retention.bytes` (time/size-bounded) | semantics differ: pick retention ≥ the longest tolerated consumer outage, which is the actual requirement MAXLEN approximates today |
| Single stream, `type` field routing | single topic with `type` header (preferred) — *not* topic-per-event-type | topic-per-type would break `fallback_handler` catch-all consumers |
| `fallback_handler` raw-fields catch-all | same, fed from record headers+value | analytics depends on it |

The backend choice should be an env switch (`BUS_BACKEND=redis|kafka`)
inside `openhis_sdk.bus`, with the constructor signature of `BusConsumer`
and the `publish_event` call shape unchanged — services must not know.

## Migration trigger — measure before deciding

ADR-0001's "~100k/day" is ≈1.2 events/s sustained — far below Redis
Streams' actual ceiling; the honest triggers are lag and retention
pressure, not raw rate. The SDK metrics module
([`libs/openhis_sdk/src/openhis_sdk/metrics.py`](../../libs/openhis_sdk/src/openhis_sdk/metrics.py))
already provides the plumbing:

- **Existing**: `openhis_dlq_depth{stream}` (scrape-time XLEN of
  `openhis:events:dlq`),
  `openhis_http_requests_total` and
  `openhis_http_request_duration_seconds` per service.
- **To add via the existing factories** (`register_callback_gauge` /
  `gauge`): `openhis_bus_stream_length` (XLEN of `openhis:events`),
  `openhis_bus_group_pending{group}` and per-group lag (XINFO GROUPS /
  XPENDING), and an `openhis_bus_events_published_total{type}` counter in
  `publish_event` once Prerequisite 0 lands.

Concrete triggers to revisit this note as an ADR:

1. Any consumer group's lag approaches `MAXLEN` (events at risk of being
   trimmed before delivery) under *normal* operation, not just outages.
2. Sustained publish rate where the required retention window no longer
   fits in Redis memory (retention is the real constraint, per ADR-0001).
3. A genuine replay/audit requirement (reprocess last N months) — Redis
   Streams fundamentally cannot serve this; Kafka's log can.

## What NOT to do

- **No dual-write.** Do not publish every event to both Redis and Kafka
  "during the transition". Dual-write creates two divergent sources of
  truth, doubles at-least-once duplicates, and turns every partial outage
  into a consistency incident. The cutover is per consumer group behind
  the SDK seam: move one consumer's backend, drain its PEL, switch its
  producer(s), done.
- **No per-service Kafka clients.** The whole value of Prerequisite 0 is
  that no service imports a bus client library directly; keep it that way.
- **No premature migration of the clinical coordination path.** ADR-0001
  scoped the Kafka option to the *analytics* pipeline. `patient.synced` /
  `lab.result.ready` latency and operational simplicity matter more than
  throughput there; they move last, if ever.
- **Do not weaken ADR-0005.** Whatever the backend, ack-on-success,
  bounded retries, a dead-letter destination with the same fields, and
  idempotent handlers are the contract — backends are swappable, the
  delivery semantics are not.
