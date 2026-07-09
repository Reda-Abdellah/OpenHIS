# Design Note — Hub-and-Spoke Multi-Site OpenHIS

> **Status: Design note — not an ADR.** This is an exploration of how a
> multi-site deployment could work on top of what already exists. Nothing
> here is decided; when a concrete phase is committed, it should be
> promoted to an ADR (next free number) with a real decision and
> consequences section.

- Date: 2026-06-12
- Relates to: [ADR-0001](../adr/0001-redis-streams-as-event-bus.md)
  (Redis Streams as event bus),
  [ADR-0003](../adr/0003-mpi-as-identity-spine.md) (MPI as identity spine),
  [ADR-0005](../adr/0005-bus-dead-letter-semantics.md) (delivery semantics)

## Problem

Today one OpenHIS instance serves one site: one Redis, one MPI, one Keycloak
realm, one nginx front door. Health networks want N site instances (each
running its own clinical apps and base stack) plus a **central instance**
that federates identity, dashboards and tele-radiology. The shape proposed
here is **hub-and-spoke**: sites keep operating autonomously; the central
instance consumes from the sites, never the reverse for clinical workflows.

## What already exists and carries over unchanged

- **The event bus contract.** All events flow through one stream
  (`openhis:events`) with a `type` field, published and consumed via
  [`libs/openhis_sdk/src/openhis_sdk/bus.py`](../../libs/openhis_sdk/src/openhis_sdk/bus.py)
  (`publish_event` / `BusConsumer`). Consumer groups give every subscriber
  an independent cursor — a central "federation forwarder" is *just another
  consumer group* on each site's stream. No producer changes.
- **At-least-once semantics.** ADR-0005 already forces handlers to be
  idempotent: ack-on-success, XAUTOCLAIM redelivery after `idle_ms`, DLQ
  (`openhis:events:dlq`) after `max_delivery` attempts. A cross-site
  forwarder inherits exactly these semantics for free — duplicates across
  the WAN are *expected* and already survivable.
- **MPI cross-referencing.** The `cross_references` table in
  [`services/mpi/database.py`](../../services/mpi/database.py) maps
  `(system, system_id) → master_id` and already carries `mrn` and
  `assigning_authority` columns. A remote site is just another `system`.
- **The PIXm/PDQm facade.** [`services/mpi/routers/fhir.py`](../../services/mpi/routers/fhir.py)
  exposes `GET /fhir/Patient` (identifier/demographic search) and
  `GET /fhir/Patient/$ihe-pix` (cross-reference query with `targetSystem`
  narrowing). This is precisely the standards-shaped surface a central MPI
  needs to answer "what is patient X called at site B?" — it exists today
  and is JWT-gated via `require_token`.
- **Analytics as a universal sink.** The analytics service consumes *every*
  event type via the SDK `fallback_handler`
  ([`services/analytics/bus_consumer.py`](../../services/analytics/bus_consumer.py)),
  so a central analytics instance can tally forwarded site events without
  knowing their schemas.

## What changes

| Concern | Today | Multi-site change |
|---|---|---|
| Bus namespacing | Single `openhis:events` stream, no site notion | Add a `site` field to the event envelope at the forwarder boundary (preferred over per-site stream names: `BusConsumer` hardcodes `STREAM` and per-site streams would multiply consumer groups). Central streams stay `openhis:events`; provenance lives in the payload. |
| Patient identity | One MPI, `system` values like `openmrs`, `openelis` | Central MPI stores site identities as `system="site:<site-id>"` (or reuses `assigning_authority`) in the existing `cross_references` table; the `$ihe-pix` `targetSystem` filter (`urn:openhis:site:<site-id>`) resolves cross-site IDs with zero schema change. |
| Auth | One Keycloak realm (`infra/keycloak/openhis-realm.json.j2`) | Two viable options: **realm-per-site** (clean blast-radius isolation, but the SDK validates one issuer per service — `openhis_sdk.auth` would need a multi-issuer JWKS cache) vs **shared realm with site roles** (no SDK change; weaker isolation). Start with shared realm + `site:*` roles; revisit at phase 2. |
| Routing | nginx generated per instance by [`platform/nginx_gen.py`](../../platform/nginx_gen.py) | Unchanged per instance. The central instance adds upstream blocks for site dashboards only if pass-through UIs are wanted (they probably are not — see phase 1). |
| Imaging | Single Orthanc ([`infra/orthanc/orthanc.json`](../../infra/orthanc/orthanc.json), `RemoteAccessAllowed: true`) | Orthanc has native `OrthancPeers` HTTP peering — site→central study push is configuration, not code. |

## Risks

- **Clock skew.** Redis stream entry IDs are minted from the *local* Redis
  server clock. The central instance must never order events from two sites
  by entry ID — ordering is only meaningful per site. Mitigation: the
  forwarder stamps `site` + original entry ID into the payload; central
  consumers treat cross-site ordering as undefined (they already must,
  given at-least-once redelivery reordering under ADR-0005).
- **Network partitions.** The forwarder is a `BusConsumer`: during a WAN
  partition, unforwarded events accumulate in the site stream up to
  `MAXLEN = 50_000` (`openhis_sdk.bus.MAXLEN`, approximate trim). A
  partition that outlasts the buffer **silently loses events** — the
  forwarder's consumer-group cursor will point at trimmed entries.
  Mitigation: monitor per-group lag (see the metrics discussion in
  [kafka-migration.md](kafka-migration.md)); for phase 2+, the central MPI
  must support reconciliation pulls via the PDQm search, not rely on the
  bus alone.
- **MRN collisions.** ADR-0003 makes the MRN the matching key. Two sites
  can assign the same MRN to different humans. The `assigning_authority`
  column exists but is not yet enforced in matching — phase 2 must scope
  every MRN lookup by site before the central MPI becomes authoritative.
- **DLQ locality.** A poison event dead-letters on the *site's*
  `openhis:events:dlq`. Central operators need site DLQ depth surfaced
  centrally (the `openhis_dlq_depth` gauge in
  [`libs/openhis_sdk/src/openhis_sdk/metrics.py`](../../libs/openhis_sdk/src/openhis_sdk/metrics.py)
  already exists per instance; federation is a Prometheus scrape-config
  problem, not a code problem).

## Phased path

1. **Read-only central dashboards.** Deploy a forwarder service at each
   site (one new `BusConsumer` group, `fallback_handler` catch-all) that
   re-publishes envelope-stamped events to the central Redis. Central runs
   only the `analytics` profile. No identity authority changes, no write
   paths — pure observation. This phase validates WAN behaviour cheaply.
2. **Central MPI as authority.** Site MPIs keep registering patients but
   forward `patient.synced` upward; the central MPI cross-references them
   (`system="site:<id>"`) and exposes `$ihe-pix` to all sites. Site
   services resolve foreign patients through the central facade. Requires
   the MRN-scoping fix above and a decision on realm-per-site.
3. **Tele-radiology.** Configure `OrthancPeers` so site Orthancs push
   selected studies to the central Orthanc; central RIS/OHIF (the existing
   `imaging` profile) reads them. The `dicom.stored` event fires centrally
   as it does today — downstream AI/analytics need no changes.

## Open questions

- Forwarder transport: direct Redis-to-Redis over WireGuard, or HTTPS
  batch POST to a central ingest endpoint (friendlier to firewalls)?
- Does phase 2 need PIXm *feed* (write) semantics, or is consuming
  forwarded `patient.synced` events sufficient?
