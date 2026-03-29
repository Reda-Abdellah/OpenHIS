# ADR-0001: Redis Streams as the Event Bus

**Status:** Accepted
**Date:** 2025-01-01

## Context

Multiple OpenHIS services need to react to clinical events (patient registered,
lab result ready, imaging order created) without tight coupling. A message bus
decouples producers from consumers and allows new services to subscribe without
modifying existing ones.

Candidates evaluated:
- **RabbitMQ** — battle-tested, AMQP protocol, dedicated management UI
- **Kafka** — high-throughput, durable log, well-suited for analytics
- **Redis Streams** — lightweight, built into the existing Redis instance

## Decision

Use **Redis Streams** with consumer groups (`XADD` / `XREADGROUP` / `XACK`).

All events are published to a single stream (`openhis:events`) with a `type`
field for routing. Each service creates its own consumer group so it processes
every event independently at its own pace.

## Consequences

- **No additional infrastructure** — Redis is already required for sessions and
  rate-limiting; adding Streams costs nothing.
- **At-least-once delivery** — Consumer group ACKs (`XACK`) ensure messages are
  redelivered if a consumer crashes before acknowledging.
- **Retention is memory-bounded** — configure `maxlen` (currently 50 000 entries)
  or enable AOF/RDB persistence to avoid silent message loss under heavy load.
- **Not suitable for high-throughput analytics** — if event volume grows beyond
  ~100 k/day, evaluate migrating the analytics pipeline to Kafka while keeping
  Redis Streams for real-time clinical coordination.

## Implementation

See [libs/openhis_sdk/src/openhis_sdk/bus.py](../../libs/openhis_sdk/src/openhis_sdk/bus.py)
for the canonical publish/consume helpers.
