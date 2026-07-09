# ADR 0005 — Event Bus Delivery Semantics: Ack-on-Success, XAUTOCLAIM Retry, Dead-Letter Queue

- Status: Accepted
- Date: 2026-06-12
- Relates to: ADR 0001 (Redis Streams as event bus)

## Context

The original `openhis_sdk.bus.BusConsumer` (and the copy-paste consumer loops in
hl7, analytics and ai-controller) acked every entry **unconditionally** after
calling the handler — the handler exception was swallowed and the entry was
`XACK`ed anyway. Any transient failure (database lock, downstream FHIR endpoint
briefly down, malformed payload) silently **lost the event**: it disappeared
from the consumer group with no retry and no trace beyond a log line.

For an integration spine carrying `patient.synced`, `lab_result.ready` and
`dicom.stored`, silent event loss is a clinical-safety problem, not just an
operational one.

## Decision

The SDK `BusConsumer` is the single place that implements delivery semantics.
All native services consume through it (mpi, hl7, ai-controller, analytics —
analytics uses the `fallback_handler` catch-all since it tallies every event
type verbatim).

1. **Ack only on success.** `BusConsumer._process` re-raises handler
   exceptions. The loop calls `XACK` only after the handler returns normally.
   A failed entry stays in the consumer group's Pending Entries List (PEL).

2. **Retry via XAUTOCLAIM.** On every loop pass, before reading new messages,
   the consumer calls `XAUTOCLAIM(openhis:events, <group>, <consumer>,
   min_idle_time=idle_ms)` (default `idle_ms=30_000`). This redelivers entries
   whose previous delivery failed — whether they were pending on this consumer
   or on a crashed peer — and increments their delivery counter.

3. **Dead-letter after N attempts.** When a handler fails and `XPENDING`
   reports the entry has been delivered `max_delivery` times (default 5), the
   entry is copied to the dead-letter stream and the original is acked so the
   group is never blocked by a poison message:

   - DLQ stream: **`openhis:events:dlq`** (`openhis_sdk.bus.DLQ_STREAM`)
   - DLQ entry fields: `origin_id`, `type`, `payload`, `error`, `group`
   - Bounded: `XADD ... MAXLEN ~ 10_000` (`openhis_sdk.bus.DLQ_MAXLEN`)
   - DLQ writes are best-effort (`send_to_dlq` never raises) — a Redis
     failure during the DLQ write must not crash the consumer loop.

4. **Unknown event types are acked.** No handler registered (and no
   `fallback_handler`) means "not for me" — success, not failure.

### Knobs

| Parameter | Default | Meaning |
|---|---|---|
| `max_delivery` | 5 | failed deliveries before the entry is dead-lettered |
| `idle_ms` | 30 000 | how long an entry must sit idle in the PEL before XAUTOCLAIM retries it |

### Operational notes

```bash
# Inspect dead-lettered events
docker exec -it openhis-redis-1 redis-cli XRANGE openhis:events:dlq - + COUNT 20

# Replay one (re-publish the original payload)
docker exec -it openhis-redis-1 redis-cli XADD openhis:events '*' type <type> payload '<payload>'
```

## Consequences

- Transient failures now self-heal: the entry is retried after `idle_ms`
  instead of being lost.
- Poison messages cannot wedge a consumer group; they surface on
  `openhis:events:dlq` with enough context (`origin_id`, `error`, `group`)
  to diagnose and replay.
- Handlers must remain **idempotent**: a handler that succeeded but crashed
  the process before `XACK` will be re-run on redelivery. This was already
  the contract (consumer groups are at-least-once); it is now actually
  exercised.
- Monitoring should watch `XLEN openhis:events:dlq` — a growing DLQ means a
  consumer is systematically failing.

## Verification

`tests/unit/sdk/test_bus.py` (fakeredis, no Docker) covers: failed entry
stays pending and unacked; redelivery to the same consumer via XAUTOCLAIM;
dead-letter + ack after `max_delivery` attempts; success acks immediately
with no DLQ entry; malformed JSON payloads dead-letter; `send_to_dlq` never
raises.
